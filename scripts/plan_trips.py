import argparse
import copy
import json
import logging
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from itertools import product
from pathlib import Path
from typing import Any, Dict, Iterable, List, Protocol, Tuple

from models import (
    DT_FMT,
    MAX_COMBO_LIMIT,
    MAX_PLAN_TIME_SECONDS,
    MAX_TRANSFER_MINUTES,
    MIN_TRANSFER_MINUTES,
    CandidateOption,
    PlanOption,
    RankedPlan,
    RelaxationSuggestion,
    RouteAnalysisResult,
    TripPlannerInput,
    TripPlannerMeta,
    TripPlannerOutput,
    get_cross_station_transfer_minutes,
)

logger = logging.getLogger("trip_planner")


class CandidateProvider(Protocol):
    def get_candidate_options(self, payload: dict) -> Dict[str, List[dict]]:
        ...


@dataclass
class Option:
    traveler: str
    strategy: str
    arrival_time: datetime
    total_minutes: int
    total_price: float
    total_transfers: int
    train_type: str
    seat_type: str
    merge_hub: str
    shared_train_ratio: float
    is_estimated: bool
    legs: List[dict]


def parse_dt(value: str) -> datetime:
    return datetime.strptime(value, DT_FMT)


def safe_time_diff_minutes(earlier: datetime, later: datetime) -> float:
    diff = (later - earlier).total_seconds()
    if diff < 0:
        diff += 86400
    return diff / 60


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def normalize_weights(weights: dict) -> dict:
    total = sum(float(v) for v in weights.values())
    if total <= 0:
        raise ValueError("weights must sum to a positive value")
    return {k: float(v) / total for k, v in weights.items()}


def build_options(raw: Dict[str, List[dict]]) -> Dict[str, List[Option]]:
    result: Dict[str, List[Option]] = {}
    for traveler, rows in raw.items():
        if not rows:
            raise ValueError(f"traveler {traveler} has no options")
        validated: List[Option] = []
        for row in rows:
            CandidateOption.model_validate(row)
            validated.append(
                Option(
                    traveler=traveler,
                    strategy=row["strategy"],
                    arrival_time=parse_dt(row["arrival_time"]),
                    total_minutes=int(row["total_minutes"]),
                    total_price=float(row["total_price"]),
                    total_transfers=int(row["total_transfers"]),
                    train_type=row["train_type"],
                    seat_type=row["seat_type"],
                    merge_hub=row.get("merge_hub", ""),
                    shared_train_ratio=float(row.get("shared_train_ratio", 0.0)),
                    is_estimated=bool(row.get("is_estimated", False)),
                    legs=row.get("legs", []),
                )
            )
        result[traveler] = validated
    return result


def robust_norm(values: List[float], reference_range: Tuple[float, float] = None) -> List[float]:
    if len(values) <= 1:
        return [0.5 for _ in values]
    lo, hi = min(values), max(values)
    if reference_range:
        lo = min(lo, reference_range[0])
        hi = max(hi, reference_range[1])
    if hi == lo:
        return [0.5 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


def check_same_train_consistency(combo: Tuple[Option, ...]) -> bool:
    same_train_strategies = {"same-train", "partial-same-train"}
    same_train_travelers = [o for o in combo if o.strategy in same_train_strategies]
    if len(same_train_travelers) < 2:
        return True
    train_nos_per_traveler = []
    for o in same_train_travelers:
        train_nos = {leg["train_no"] for leg in o.legs}
        train_nos_per_traveler.append(train_nos)
    common = set.intersection(*train_nos_per_traveler)
    return len(common) > 0


def fix_same_train_consistency(combo: Tuple[Option, ...]) -> Tuple[Option, ...]:
    same_train_strategies = {"same-train", "partial-same-train"}
    same_train_travelers = [o for o in combo if o.strategy in same_train_strategies]
    if len(same_train_travelers) < 2:
        return combo
    train_nos_per_traveler = []
    for o in same_train_travelers:
        train_nos = {leg["train_no"] for leg in o.legs}
        train_nos_per_traveler.append(train_nos)
    common = set.intersection(*train_nos_per_traveler)
    if len(common) > 0:
        return combo
    all_train_nos = set()
    for o in same_train_travelers:
        all_train_nos |= {leg["train_no"] for leg in o.legs}
    pairwise_overlap = False
    for i in range(len(same_train_travelers)):
        for j in range(i + 1, len(same_train_travelers)):
            t1 = {leg["train_no"] for leg in same_train_travelers[i].legs}
            t2 = {leg["train_no"] for leg in same_train_travelers[j].legs}
            if t1 & t2:
                pairwise_overlap = True
                break
        if pairwise_overlap:
            break
    fixed = []
    for o in combo:
        if o.strategy not in same_train_strategies:
            fixed.append(o)
            continue
        my_train_nos = {leg["train_no"] for leg in o.legs}
        if pairwise_overlap:
            best_ratio = 0.0
            for other_o in combo:
                if other_o is o:
                    continue
                other_train_nos = {leg["train_no"] for leg in other_o.legs}
                overlap = my_train_nos & other_train_nos
                if overlap:
                    ratio = len(overlap) / len(my_train_nos)
                    best_ratio = max(best_ratio, ratio)
            fixed.append(replace(o,
                strategy="partial-same-train",
                shared_train_ratio=best_ratio,
            ))
        else:
            fixed.append(replace(o,
                strategy="synchronized-arrival",
                shared_train_ratio=0.0,
            ))
    return tuple(fixed)


def check_transfer_time_validity(combo: Tuple[Option, ...]) -> bool:
    for o in combo:
        for i in range(len(o.legs) - 1):
            arr = parse_dt(o.legs[i]["arr_time"])
            dep = parse_dt(o.legs[i + 1]["dep_time"])
            gap_minutes = safe_time_diff_minutes(arr, dep)
            arr_station = o.legs[i].get("arr_station", "")
            dep_station = o.legs[i + 1].get("dep_station", "")
            if arr_station and dep_station and arr_station != dep_station:
                cross_time = get_cross_station_transfer_minutes(arr_station, dep_station)
                if cross_time is not None:
                    min_required = cross_time
                else:
                    min_required = MIN_TRANSFER_MINUTES + 15
            else:
                min_required = MIN_TRANSFER_MINUTES
            if gap_minutes < min_required:
                return False
            if gap_minutes > MAX_TRANSFER_MINUTES:
                return False
    return True


def compute_arrival_spread_minutes(arrivals: List[datetime]) -> int:
    if len(arrivals) <= 1:
        return 0
    sorted_arr = sorted(arrivals)
    earliest = sorted_arr[0]
    adjusted = []
    for a in sorted_arr:
        delta = (a - earliest).total_seconds()
        if delta < 0 and delta > -18 * 3600:
            adjusted.append(a + timedelta(days=1))
        elif delta < -18 * 3600:
            adjusted.append(a)
        else:
            adjusted.append(a)
    adjusted.sort()
    max_diff = 0
    for i in range(len(adjusted)):
        for j in range(i + 1, len(adjusted)):
            diff = (adjusted[j] - adjusted[i]).total_seconds()
            if diff > 24 * 3600:
                diff = 24 * 3600 - diff
            if diff > max_diff:
                max_diff = diff
    return int(max_diff / 60)


def validate_combo_constraints(
    combo: Tuple[Option, ...],
    max_arrival_diff_minutes: int,
    max_transfers: int,
    latest_arrival: datetime | None,
    departure_windows: Dict[str, Tuple[datetime, datetime]],
    must_train_types: set[str],
    accept_standing: bool,
    allowed_transfer_hubs: set[str],
    traveler_preferences: Dict[str, dict],
) -> Tuple[bool, Tuple[Option, ...]]:
    arrivals = [o.arrival_time for o in combo]
    arrival_diff = compute_arrival_spread_minutes(arrivals)
    if arrival_diff > max_arrival_diff_minutes:
        return False, combo
    if sum(o.total_transfers for o in combo) > max_transfers:
        return False, combo
    if latest_arrival and any(a > latest_arrival for a in arrivals):
        return False, combo
    combo = fix_same_train_consistency(combo)
    if not check_transfer_time_validity(combo):
        return False, combo
    for o in combo:
        if not o.legs:
            return False, combo
        if must_train_types and o.train_type not in must_train_types:
            return False, combo
        if not accept_standing and o.seat_type.lower() in {"standing", "no-seat", "wz"}:
            return False, combo
        if allowed_transfer_hubs and o.merge_hub and o.merge_hub not in allowed_transfer_hubs:
            return False, combo
        first_dep = parse_dt(o.legs[0]["dep_time"])
        left, right = departure_windows[o.traveler]
        if first_dep < left or first_dep > right:
            return False, combo
        pref = traveler_preferences.get(o.traveler, {})
        max_transfer_for_traveler = int(pref.get("max_transfers", 99))
        if o.total_transfers > max_transfer_for_traveler:
            return False, combo
        no_transfer = bool(pref.get("avoid_transfer", False))
        if no_transfer and o.total_transfers > 0:
            return False, combo
    return True, combo


def count_total_combinations(options_by_traveler: Dict[str, List[Option]], travelers: List[str]) -> int:
    total = 1
    for t in travelers:
        total *= len(options_by_traveler[t])
    return total


def iter_bounded_combos(
    options_by_traveler: Dict[str, List[Option]], travelers: List[str]
) -> Iterable[Tuple[Option, ...]]:
    total = count_total_combinations(options_by_traveler, travelers)
    if total > MAX_COMBO_LIMIT:
        raise ValueError(
            f"combination explosion: {total} exceeds limit {MAX_COMBO_LIMIT}, "
            "please pre-filter candidate options"
        )
    return product(*(options_by_traveler[t] for t in travelers))


def generate_reasoning(plan: dict, rank: int) -> str:
    reasons = []
    strategies = plan.get("strategy_mix", [])
    spread = plan.get("arrival_spread_minutes", 0)
    transfers = plan.get("total_transfers", 0)
    score = plan.get("score", 0)

    all_same_train = all(s in ("same-train", "partial-same-train") for s in strategies)
    has_same_train = any(s in ("same-train", "partial-same-train") for s in strategies)
    has_partial = "partial-same-train" in strategies
    has_transfer_merge = "transfer-merge" in strategies
    has_sync = "synchronized-arrival" in strategies
    has_best_compromise = plan.get("is_best_compromise", False)

    if has_best_compromise:
        reasons.append("综合最优折中方案（混合多种策略）")
    elif all_same_train and len(strategies) > 0:
        reasons.append("全程同车")
    elif has_partial:
        reasons.append("部分同车（中转后汇合同车）")
    elif has_same_train:
        reasons.append("部分同车")
    elif has_transfer_merge:
        reasons.append("中转汇合")
    elif has_sync:
        reasons.append("独立车次同步到达")

    if spread == 0:
        reasons.append("完美同步到达")
    elif spread <= 15:
        reasons.append("到达时间差极小")
    elif spread <= 30:
        reasons.append("到达时间差可接受")
    if transfers == 0:
        reasons.append("零换乘")
    elif transfers <= 1:
        reasons.append("换乘少")

    options = plan.get("options", [])
    has_standing = any(o.get("seat_type", "").lower() in {"standing", "no-seat", "wz"} for o in options)
    if not has_standing:
        reasons.append("全程有座")

    shared_trains = set()
    for opt in options:
        for leg in opt.get("legs", []):
            shared_trains.add(leg.get("train_no", ""))
    if len(options) > 1 and len(shared_trains) < sum(len(o.get("legs", [])) for o in options):
        common_trains = None
        for opt in options:
            opt_trains = {leg.get("train_no") for leg in opt.get("legs", [])}
            if common_trains is None:
                common_trains = opt_trains
            else:
                common_trains = common_trains & opt_trains
        if common_trains:
            reasons.append(f"共享车次: {', '.join(sorted(common_trains))}")

    if rank == 0:
        reasons.append("综合评分最优")
    elif score >= 80:
        reasons.append("高质量方案")
    elif score >= 60:
        reasons.append("性价比方案")

    return "；".join(reasons) if reasons else "综合评分方案"


def _ensure_diversity(ranked: List[dict], topk: int) -> List[dict]:
    if not ranked or topk <= 1:
        return ranked[:topk]
    result = [ranked[0]]
    seen_train_keys = set()
    seen_strategy_keys = set()

    def _plan_train_key(plan: dict) -> tuple:
        train_nos = []
        for opt in plan.get("options", []):
            for leg in opt.get("legs", []):
                train_nos.append(leg.get("train_no", ""))
        if train_nos:
            return tuple(train_nos)
        return (
            tuple(sorted(plan.get("strategy_mix", []))),
            round(plan.get("total_price", 0), 0),
            plan.get("total_minutes", 0),
            plan.get("arrival_spread_minutes", 0),
        )

    def _plan_fingerprint(plan: dict) -> tuple:
        return (
            tuple(sorted(plan.get("strategy_mix", []))),
            round(plan.get("total_price", 0), 0),
            plan.get("total_minutes", 0),
            plan.get("arrival_spread_minutes", 0),
        )

    def _shared_train_key(plan: dict) -> tuple:
        shared = []
        for opt in plan.get("options", []):
            for leg in opt.get("legs", []):
                shared.append(leg.get("train_no", ""))
        return tuple(sorted(set(shared)))

    train_key0 = _plan_train_key(ranked[0])
    seen_train_keys.add(train_key0)
    s0 = tuple(sorted(ranked[0].get("strategy_mix", [])))
    seen_strategy_keys.add(s0)

    for plan in ranked[1:]:
        if len(result) >= topk:
            break
        strategy_key = tuple(sorted(plan.get("strategy_mix", [])))
        train_key = _plan_train_key(plan)
        if train_key in seen_train_keys:
            continue
        if strategy_key not in seen_strategy_keys:
            result.append(plan)
            seen_strategy_keys.add(strategy_key)
            seen_train_keys.add(train_key)
            continue

    for plan in ranked[1:]:
        if len(result) >= topk:
            break
        if plan in result:
            continue
        train_key = _plan_train_key(plan)
        if train_key in seen_train_keys:
            continue
        shared_key = _shared_train_key(plan)
        dominated = False
        for existing in result:
            ex_shared = _shared_train_key(existing)
            if shared_key == ex_shared:
                dominated = True
                break
        if dominated:
            continue
        plan_price = plan.get("total_price", 0)
        plan_time = plan.get("total_minutes", 0)
        plan_spread = plan.get("arrival_spread_minutes", 0)
        too_similar = False
        for existing in result:
            ex_price = existing.get("total_price", 0)
            ex_time = existing.get("total_minutes", 0)
            ex_spread = existing.get("arrival_spread_minutes", 0)
            price_diff = abs(plan_price - ex_price) / max(ex_price, 1)
            time_diff = abs(plan_time - ex_time) / max(ex_time, 1)
            spread_diff = abs(plan_spread - ex_spread)
            if price_diff < 0.05 and time_diff < 0.05 and spread_diff < 15:
                too_similar = True
                break
        if not too_similar:
            result.append(plan)
            seen_train_keys.add(train_key)

    for plan in ranked[1:]:
        if len(result) >= topk:
            break
        train_key = _plan_train_key(plan)
        if train_key not in seen_train_keys and plan not in result:
            result.append(plan)
            seen_train_keys.add(train_key)
    return result[:topk]


def compute_real_shared_ratio(combo: Tuple[Option, ...]) -> float:
    if len(combo) <= 1:
        return 0.0
    all_train_nos = []
    for o in combo:
        train_nos = [leg.get("train_no", "") for leg in o.legs]
        all_train_nos.append(train_nos)
    total_legs = sum(len(legs) for legs in all_train_nos)
    if total_legs == 0:
        return 0.0
    shared_count = 0
    for i in range(len(all_train_nos)):
        for j in range(i + 1, len(all_train_nos)):
            common = set(all_train_nos[i]) & set(all_train_nos[j])
            shared_count += len(common)
    max_possible_shared = len(combo) * (len(combo) - 1) / 2
    if max_possible_shared == 0:
        return 0.0
    avg_shared_per_pair = shared_count / max_possible_shared
    avg_legs_per_traveler = total_legs / len(combo)
    if avg_legs_per_traveler == 0:
        return 0.0
    ratio = min(1.0, avg_shared_per_pair / avg_legs_per_traveler)
    return round(ratio, 4)


def score_combinations(
    combos: List[Tuple[Option, ...]],
    weights: dict,
    priority_weights: Dict[str, float],
) -> List[dict]:
    if not combos:
        return []

    travel_times = [sum(o.total_minutes for o in c) for c in combos]
    prices = [sum(o.total_price for o in c) for c in combos]
    transfers = [sum(o.total_transfers for o in c) for c in combos]
    arrival_diffs = [compute_arrival_spread_minutes([o.arrival_time for o in c]) for c in combos]
    shared_ratios = [compute_real_shared_ratio(c) for c in combos]
    comfort_penalties = [
        sum(1 for o in c if o.seat_type.lower() in {"standing", "no-seat", "wz"})
        + sum(o.total_transfers for o in c)
        for c in combos
    ]
    priority_scores = []
    for c in combos:
        ps = 0.0
        for o in c:
            weight = priority_weights.get(o.traveler, 1.0)
            traveler_quality = 1.0 - min(o.total_transfers, 3) / 3.0
            if o.seat_type.lower() in {"standing", "no-seat", "wz"}:
                traveler_quality -= 0.5
            ps += weight * traveler_quality
        priority_scores.append(ps)

    unknown_train_penalties = []
    estimated_penalties = []
    for c in combos:
        has_unknown = any(
            leg.get("train_no", "").startswith("G_UNKNOWN")
            for o in c
            for leg in o.legs
        )
        unknown_train_penalties.append(1.0 if has_unknown else 0.0)
        has_estimated = any(o.is_estimated for o in c)
        estimated_penalties.append(0.5 if has_estimated else 0.0)

    t_norm = robust_norm(travel_times)
    p_norm = robust_norm(prices)
    x_norm = robust_norm(transfers)
    d_norm = robust_norm(arrival_diffs)
    s_norm = robust_norm(shared_ratios)
    c_norm = robust_norm(comfort_penalties)
    pr_norm = robust_norm(priority_scores)
    u_norm = robust_norm(unknown_train_penalties, reference_range=(0.0, 1.0))
    e_norm = robust_norm(estimated_penalties, reference_range=(0.0, 0.5))

    unknown_penalty_weight = 0.15
    estimated_penalty_weight = 0.05
    total_weight = sum(weights.values()) + unknown_penalty_weight + estimated_penalty_weight
    adjusted_weights = {k: v / total_weight for k, v in weights.items()}
    adjusted_unknown = unknown_penalty_weight / total_weight
    adjusted_estimated = estimated_penalty_weight / total_weight

    ranked = []
    for i, combo in enumerate(combos):
        penalty = 0.0
        penalty += adjusted_weights["travel_time"] * t_norm[i]
        penalty += adjusted_weights["price"] * p_norm[i]
        penalty += adjusted_weights["transfer_penalty"] * x_norm[i]
        penalty += adjusted_weights["arrival_sync"] * d_norm[i]
        penalty += adjusted_weights["comfort_penalty"] * c_norm[i]
        penalty += adjusted_unknown * u_norm[i]
        penalty += adjusted_estimated * e_norm[i]
        bonus = 0.0
        bonus += adjusted_weights["priority_satisfaction"] * pr_norm[i]
        bonus += adjusted_weights["same_train_bonus"] * s_norm[i]

        score = 50.0 + (bonus - penalty) * 100.0
        score = max(0.0, min(100.0, score))

        strategy_set = sorted({o.strategy for o in combo})
        plan = {
            "score": round(score, 2),
            "strategy_mix": strategy_set,
            "is_best_compromise": False,
            "arrival_spread_minutes": arrival_diffs[i],
            "total_minutes": travel_times[i],
            "total_price": round(prices[i], 2),
            "total_transfers": transfers[i],
            "comfort_penalty": comfort_penalties[i],
            "reasoning": "",
            "route_analysis": None,
            "options": [
                {
                    "traveler": o.traveler,
                    "strategy": o.strategy,
                    "arrival_time": o.arrival_time.strftime(DT_FMT),
                    "total_minutes": o.total_minutes,
                    "total_price": o.total_price,
                    "total_transfers": o.total_transfers,
                    "train_type": o.train_type,
                    "seat_type": o.seat_type,
                    "merge_hub": o.merge_hub,
                    "shared_train_ratio": o.shared_train_ratio,
                    "is_estimated": o.is_estimated,
                    "legs": o.legs,
                }
                for o in combo
            ],
        }
        ranked.append(plan)

    ranked.sort(key=lambda x: x["score"], reverse=True)

    best_compromise_idx = -1
    best_compromise_score = -1
    for i, plan in enumerate(ranked):
        mix = plan.get("strategy_mix", [])
        if len(mix) >= 2:
            if plan["score"] > best_compromise_score:
                best_compromise_score = plan["score"]
                best_compromise_idx = i
    if best_compromise_idx >= 0:
        ranked[best_compromise_idx]["is_best_compromise"] = True

    for i, plan in enumerate(ranked):
        plan["reasoning"] = generate_reasoning(plan, i)
    return ranked


def suggest_relaxations(data: dict, original_result: dict) -> List[dict]:
    if original_result.get("total_feasible", 0) > 0:
        return []
    suggestions = []
    constraints = data.get("constraints", {})

    relaxed = copy.deepcopy(data)
    current = constraints.get("max_arrival_time_diff_minutes", 60)
    new_val = current + 30
    relaxed["constraints"]["max_arrival_time_diff_minutes"] = new_val
    try:
        result = plan_trip_data(relaxed, topk=1, input_name="relaxation_check", _skip_relaxation=True)
        if result.get("total_feasible", 0) > 0:
            suggestions.append({
                "dimension": "max_arrival_time_diff_minutes",
                "current_value": current,
                "suggested_value": new_val,
                "description": f"将到达时间差从 {current} 分钟放宽到 {new_val} 分钟",
                "feasible_count_after": result["total_feasible"],
            })
    except Exception:
        pass

    relaxed = copy.deepcopy(data)
    current = constraints.get("max_transfers", 99)
    new_val = current + 1
    relaxed["constraints"]["max_transfers"] = new_val
    try:
        result = plan_trip_data(relaxed, topk=1, input_name="relaxation_check", _skip_relaxation=True)
        if result.get("total_feasible", 0) > 0:
            suggestions.append({
                "dimension": "max_transfers",
                "current_value": current,
                "suggested_value": new_val,
                "description": f"将最大换乘次数从 {current} 放宽到 {new_val}",
                "feasible_count_after": result["total_feasible"],
            })
    except Exception:
        pass

    if constraints.get("latest_arrival"):
        relaxed = copy.deepcopy(data)
        current_arrival = constraints["latest_arrival"]
        arr_dt = parse_dt(current_arrival)
        new_arrival = (arr_dt + timedelta(hours=2)).strftime(DT_FMT)
        relaxed["constraints"]["latest_arrival"] = new_arrival
        try:
            result = plan_trip_data(relaxed, topk=1, input_name="relaxation_check", _skip_relaxation=True)
            if result.get("total_feasible", 0) > 0:
                suggestions.append({
                    "dimension": "latest_arrival",
                    "current_value": current_arrival,
                    "suggested_value": new_arrival,
                    "description": f"将最晚到达时间从 {current_arrival} 放宽到 {new_arrival}",
                    "feasible_count_after": result["total_feasible"],
                })
        except Exception:
            pass

    if not constraints.get("accept_standing", True):
        relaxed = copy.deepcopy(data)
        relaxed["constraints"]["accept_standing"] = True
        try:
            result = plan_trip_data(relaxed, topk=1, input_name="relaxation_check", _skip_relaxation=True)
            if result.get("total_feasible", 0) > 0:
                suggestions.append({
                    "dimension": "accept_standing",
                    "current_value": False,
                    "suggested_value": True,
                    "description": "允许无座票",
                    "feasible_count_after": result["total_feasible"],
                })
        except Exception:
            pass

    for traveler_data in data.get("travelers", []):
        name = traveler_data.get("name", "")
        earliest = traveler_data.get("earliest_departure", "")
        latest = traveler_data.get("latest_departure", "")
        if not earliest or not latest:
            continue
        try:
            earliest_dt = parse_dt(earliest)
            latest_dt = parse_dt(latest)
            window_hours = (latest_dt - earliest_dt).total_seconds() / 3600
            if window_hours < 6:
                relaxed = copy.deepcopy(data)
                new_earliest = (earliest_dt - timedelta(hours=2)).strftime(DT_FMT)
                new_latest = (latest_dt + timedelta(hours=2)).strftime(DT_FMT)
                for t in relaxed.get("travelers", []):
                    if t.get("name") == name:
                        t["earliest_departure"] = new_earliest
                        t["latest_departure"] = new_latest
                        break
                result = plan_trip_data(relaxed, topk=1, input_name="relaxation_check", _skip_relaxation=True)
                if result.get("total_feasible", 0) > 0:
                    suggestions.append({
                        "dimension": f"departure_window_{name}",
                        "current_value": f"{earliest} ~ {latest}",
                        "suggested_value": f"{new_earliest} ~ {new_latest}",
                        "description": f"将 {name} 的出发时间窗口从 {int(window_hours)}h 扩展到 {int(window_hours+4)}h",
                        "feasible_count_after": result["total_feasible"],
                    })
        except Exception:
            pass

    if constraints.get("must_train_types"):
        relaxed = copy.deepcopy(data)
        relaxed["constraints"]["must_train_types"] = []
        try:
            result = plan_trip_data(relaxed, topk=1, input_name="relaxation_check", _skip_relaxation=True)
            if result.get("total_feasible", 0) > 0:
                suggestions.append({
                    "dimension": "must_train_types",
                    "current_value": constraints["must_train_types"],
                    "suggested_value": [],
                    "description": "取消列车类型限制，允许所有车型",
                    "feasible_count_after": result["total_feasible"],
                })
        except Exception:
            pass

    return suggestions


def plan_trip_data(data: dict, topk: int = 3, input_name: str = "in_memory", _skip_relaxation: bool = False, data_source: str = "mock") -> dict:
    start_time = time.time()
    validated = TripPlannerInput.model_validate(data)
    departure_windows: Dict[str, Tuple[datetime, datetime]] = {}
    for t in validated.travelers:
        departure_windows[t.name] = (
            parse_dt(t.earliest_departure),
            parse_dt(t.latest_departure),
        )

    weights = normalize_weights(data["weights"])
    options_by_traveler = build_options(data["candidate_options"])
    travelers = sorted(options_by_traveler.keys())
    if not travelers:
        raise ValueError("candidate_options is empty")
    for t in travelers:
        if t not in departure_windows:
            raise ValueError(f"traveler {t} missing in travelers list")

    constraints = validated.constraints
    max_arrival_diff = constraints.max_arrival_time_diff_minutes
    max_transfers = constraints.max_transfers
    latest_arrival = parse_dt(constraints.latest_arrival) if constraints.latest_arrival else None
    must_train_types = set(constraints.must_train_types)
    accept_standing = constraints.accept_standing
    allowed_transfer_hubs = set(constraints.allowed_transfer_hubs)
    traveler_preferences: Dict[str, dict] = {
        t.name: t.preferences.model_dump(exclude_none=True) for t in validated.travelers
    }
    priority_weights: Dict[str, float] = {t.name: t.priority_weight for t in validated.travelers}

    total_combos = count_total_combinations(options_by_traveler, travelers)
    if total_combos > MAX_COMBO_LIMIT:
        raise ValueError(
            f"combination explosion: {total_combos} exceeds limit {MAX_COMBO_LIMIT}, "
            "please pre-filter candidate options"
        )
    logger.info("Will evaluate %s raw combinations", total_combos)

    feasible_combos: List[Tuple[Option, ...]] = []
    checked_count = 0
    timed_out = False
    for c in product(*(options_by_traveler[t] for t in travelers)):
        checked_count += 1
        if time.time() - start_time > MAX_PLAN_TIME_SECONDS:
            logger.warning(
                "Planning timeout after checking %d/%d combos",
                checked_count,
                total_combos,
            )
            timed_out = True
            break
        is_valid, fixed_combo = validate_combo_constraints(
            combo=c,
            max_arrival_diff_minutes=max_arrival_diff,
            max_transfers=max_transfers,
            latest_arrival=latest_arrival,
            departure_windows=departure_windows,
            must_train_types=must_train_types,
            accept_standing=accept_standing,
            allowed_transfer_hubs=allowed_transfer_hubs,
            traveler_preferences=traveler_preferences,
        )
        if is_valid:
            feasible_combos.append(fixed_combo)

    feasible_count = len(feasible_combos)
    logger.info("Kept %s feasible combinations out of %s checked", feasible_count, checked_count)
    planning_time = time.time() - start_time

    data_source_reliability = "low" if data_source == "mock" else "medium"
    relaxed_constraints_applied = []

    if not feasible_combos:
        relaxed_constraints_applied = []
        relaxed_max_arrival = max_arrival_diff
        while not feasible_combos and relaxed_max_arrival < 1440:
            relaxed_max_arrival = min(relaxed_max_arrival * 2, 1440)
            relaxed_constraints_applied.append(f"max_arrival_time_diff_minutes: {max_arrival_diff} -> {relaxed_max_arrival}")
            for c in product(*(options_by_traveler[t] for t in travelers)):
                is_valid, fixed_combo = validate_combo_constraints(
                    combo=c,
                    max_arrival_diff_minutes=relaxed_max_arrival,
                    max_transfers=max_transfers,
                    latest_arrival=latest_arrival,
                    departure_windows=departure_windows,
                    must_train_types=must_train_types,
                    accept_standing=accept_standing,
                    allowed_transfer_hubs=allowed_transfer_hubs,
                    traveler_preferences=traveler_preferences,
                )
                if is_valid:
                    feasible_combos.append(fixed_combo)
            if feasible_combos:
                break
            if relaxed_max_arrival >= 1440:
                break

        if feasible_combos:
            feasible_count = len(feasible_combos)
            logger.info(
                "Auto-relaxed constraints to find %d feasible combinations: %s",
                len(feasible_combos),
                relaxed_constraints_applied,
            )
        else:
            relaxation_suggestions_raw = suggest_relaxations(data, {"total_feasible": 0}) if not _skip_relaxation else []
            relaxation_suggestions = [RelaxationSuggestion(**s) for s in relaxation_suggestions_raw]

            route_analysis = RouteAnalysisResult(
                is_valid=True,
                data_source=data_source,
                data_source_reliability=data_source_reliability,
                warnings=["Using mock data - not real-time. Integrate 12306/Ctrip API for production use."] if data_source == "mock" else [],
            )

            output = TripPlannerOutput(
                top_plans=[],
                total_feasible=0,
                message="No feasible itinerary under constraints",
                relaxation_suggestions=relaxation_suggestions,
                route_analysis=route_analysis,
                meta=TripPlannerMeta(
                    input_file=input_name,
                    generated_at=datetime.now().strftime(DT_FMT),
                    planning_time_seconds=round(planning_time, 3),
                    total_combinations=total_combos,
                    feasible_combinations=0,
                    timed_out=timed_out,
                    constraints=constraints.model_dump(),
                    weights=weights,
                    data_source=data_source,
                    data_source_reliability=data_source_reliability,
                ),
            )
            return output.model_dump()

    ranked = score_combinations(
        combos=feasible_combos,
        weights=weights,
        priority_weights=priority_weights,
    )

    top_plans_raw = _ensure_diversity(ranked, topk)

    try:
        from route_analyzer import analyze_plan
        travelers_data = [t.model_dump() for t in validated.travelers]
        route_analysis_result = analyze_plan(top_plans_raw, travelers_data, validated.destination, data_source)
    except Exception as e:
        logger.debug("Route analysis failed: %s", e)
        route_analysis_result = RouteAnalysisResult(
            is_valid=True,
            data_source=data_source,
            data_source_reliability=data_source_reliability,
            warnings=["Route analysis unavailable"] if data_source == "mock" else [],
        )

    if relaxed_constraints_applied:
        existing_warnings = list(route_analysis_result.warnings) if route_analysis_result.warnings else []
        existing_warnings.append(
            f"Constraints auto-relaxed to find feasible plans: {'; '.join(relaxed_constraints_applied)}. "
            "Results may not meet original sync requirements."
        )
        route_analysis_result = RouteAnalysisResult(
            is_valid=route_analysis_result.is_valid,
            data_source=route_analysis_result.data_source,
            data_source_reliability=route_analysis_result.data_source_reliability,
            issues=route_analysis_result.issues,
            warnings=existing_warnings,
        )

    top_plans = [RankedPlan(**r) for r in top_plans_raw]
    output = TripPlannerOutput(
        top_plans=top_plans,
        total_feasible=len(ranked),
        route_analysis=route_analysis_result,
        meta=TripPlannerMeta(
            input_file=input_name,
            generated_at=datetime.now().strftime(DT_FMT),
            planning_time_seconds=round(planning_time, 3),
            total_combinations=total_combos,
            feasible_combinations=feasible_count,
            timed_out=timed_out,
            constraints=constraints.model_dump(),
            weights=weights,
            data_source=data_source,
            data_source_reliability=data_source_reliability,
        ),
    )
    return output.model_dump()


def main():
    parser = argparse.ArgumentParser(description="Multi-user Trip Planner")
    parser.add_argument("--input", required=True, help="Path to input JSON file")
    parser.add_argument("--output", default="", help="Path to output JSON file (default: stdout)")
    parser.add_argument("--topk", type=int, default=3, help="Number of top plans to return")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    parser.add_argument("--data-source", default="mock", help="Data source identifier (mock/api)")
    args = parser.parse_args()

    setup_logging(args.log_level)
    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    data = json.loads(input_path.read_text(encoding="utf-8"))
    result = plan_trip_data(data=data, topk=args.topk, input_name=str(input_path), data_source=args.data_source)

    output_json = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(output_json, encoding="utf-8")
        logger.info("Output written to %s", args.output)
    else:
        print(output_json)


if __name__ == "__main__":
    main()
