import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from models import (
    DT_FMT,
    ALL_KNOWN_STATIONS,
    HSR_PRIMARY_STATION,
    STATION_ALIASES,
    RouteAnalysisIssue,
    RouteAnalysisResult,
    get_cross_station_transfer_minutes,
    is_known_station,
    station_belongs_to_city,
    validate_station_for_city,
)

logger = logging.getLogger("trip_planner.route_analyzer")


HSR_PRICE_PER_KM = {
    "second-class": 0.46,
    "first-class": 0.77,
    "business-class": 1.45,
}

HSR_SPEED_KMH = {
    "G": 300,
    "D": 200,
    "C": 200,
}

APPROX_DISTANCES_KM: Dict[Tuple[str, str], int] = {
    ("Guangzhou", "Beijing"): 2298,
    ("Guangzhou", "Wuhan"): 1069,
    ("Guangzhou", "Zhengzhou"): 1605,
    ("Guangzhou", "Changsha"): 707,
    ("Guangzhou", "Shenzhen"): 102,
    ("Wuhan", "Beijing"): 1229,
    ("Wuhan", "Zhengzhou"): 536,
    ("Wuhan", "Shanghai"): 837,
    ("Wuhan", "Changsha"): 362,
    ("Zhengzhou", "Beijing"): 693,
    ("Zhengzhou", "Shanghai"): 998,
    ("Changsha", "Beijing"): 1591,
    ("Shanghai", "Beijing"): 1318,
    ("Shenzhen", "Beijing"): 2400,
    ("Shenzhen", "Wuhan"): 1171,
    ("Chengdu", "Beijing"): 1874,
    ("Chengdu", "Zhengzhou"): 1181,
    ("Hangzhou", "Beijing"): 1279,
    ("Nanjing", "Beijing"): 1023,
    ("Shijiazhuang", "Beijing"): 281,
    ("Xi'an", "Beijing"): 1200,
    ("Jinan", "Beijing"): 406,
    ("Hefei", "Beijing"): 960,
    ("Nanchang", "Beijing"): 1440,
    ("Fuzhou", "Beijing"): 1980,
    ("Chongqing", "Beijing"): 1800,
    ("Tianjin", "Beijing"): 120,
    ("Guangzhou", "Shanghai"): 1530,
    ("Guangzhou", "Nanjing"): 1370,
    ("Wuhan", "Chengdu"): 1100,
}


def _get_distance(origin: str, destination: str) -> Optional[int]:
    dist = APPROX_DISTANCES_KM.get((origin, destination))
    if dist:
        return dist
    dist = APPROX_DISTANCES_KM.get((destination, origin))
    return dist


def _estimate_price(distance_km: int, seat_type: str = "second-class") -> float:
    rate = HSR_PRICE_PER_KM.get(seat_type, HSR_PRICE_PER_KM["second-class"])
    return round(distance_km * rate, 2)


def _estimate_duration(distance_km: int, train_prefix: str = "G") -> float:
    speed = HSR_SPEED_KMH.get(train_prefix, 250)
    return round(distance_km / speed, 1)


def analyze_route_option(
    option: dict,
    origin_city: str,
    destination_city: str,
    data_source: str = "mock",
) -> RouteAnalysisResult:
    issues: List[RouteAnalysisIssue] = []
    warnings: List[str] = []
    is_valid = True

    if data_source == "mock":
        warnings.append("Data from mock provider. For real-time schedules, integrate 12306/Ctrip API.")

    legs = option.get("legs", [])
    if not legs:
        issues.append(RouteAnalysisIssue(
            severity="error", dimension="legs",
            message="No legs found in option",
        ))
        return RouteAnalysisResult(
            is_valid=False, data_source=data_source,
            data_source_reliability="low" if data_source == "mock" else "medium",
            issues=issues, warnings=warnings,
        )

    first_leg = legs[0]
    dep_station = first_leg.get("dep_station", "")
    valid, msg = validate_station_for_city(dep_station, origin_city)
    if not valid:
        issues.append(RouteAnalysisIssue(
            severity="error", dimension="departure_station",
            message=msg,
        ))
        is_valid = False
    elif msg:
        warnings.append(msg)

    last_leg = legs[-1]
    arr_station = last_leg.get("arr_station", "")
    valid, msg = validate_station_for_city(arr_station, destination_city)
    if not valid:
        issues.append(RouteAnalysisIssue(
            severity="error", dimension="arrival_station",
            message=msg,
        ))
        is_valid = False
    elif msg:
        warnings.append(msg)

    for i, leg in enumerate(legs):
        leg_dep_station = leg.get("dep_station", "")
        leg_arr_station = leg.get("arr_station", "")
        if not is_known_station(leg_dep_station) and leg_dep_station:
            warnings.append(f"Leg {i+1} departure station '{leg_dep_station}' not in verified station database")
        if not is_known_station(leg_arr_station) and leg_arr_station:
            warnings.append(f"Leg {i+1} arrival station '{leg_arr_station}' not in verified station database")

    total_price = option.get("total_price", 0)
    total_minutes = option.get("total_minutes", 0)
    distance = _get_distance(origin_city, destination_city)

    if distance and total_price > 0:
        estimated_price = _estimate_price(distance, option.get("seat_type", "second-class"))
        if option.get("total_transfers", 0) > 0:
            estimated_price *= 1.05
        price_ratio = total_price / estimated_price if estimated_price > 0 else 1.0
        if price_ratio < 0.5 or price_ratio > 2.0:
            issues.append(RouteAnalysisIssue(
                severity="warning", dimension="price",
                message=f"Price CNY {total_price} seems unusual for {origin_city}->{destination_city} "
                        f"(estimated ~CNY {estimated_price}, ratio={price_ratio:.2f})",
            ))
            warnings.append(f"Price deviation: {price_ratio:.1%} of estimated")

    if distance and total_minutes > 0:
        train_prefix = legs[0].get("train_no", "G")[0] if legs else "G"
        estimated_hours = _estimate_duration(distance, train_prefix)
        estimated_minutes = estimated_hours * 60
        if option.get("total_transfers", 0) > 0:
            estimated_minutes += 30
        actual_hours = total_minutes / 60
        if actual_hours > estimated_hours * 2:
            issues.append(RouteAnalysisIssue(
                severity="warning", dimension="duration",
                message=f"Travel time {total_minutes}min seems too long for {origin_city}->{destination_city} "
                        f"(estimated ~{int(estimated_minutes)}min)",
            ))

    for i in range(len(legs) - 1):
        try:
            arr_time = datetime.strptime(legs[i]["arr_time"], DT_FMT)
            dep_time = datetime.strptime(legs[i + 1]["dep_time"], DT_FMT)
            gap = (dep_time - arr_time).total_seconds() / 60
            arr_station = legs[i].get("arr_station", "")
            dep_station = legs[i + 1].get("dep_station", "")
            if arr_station != dep_station:
                cross_time = get_cross_station_transfer_minutes(arr_station, dep_station)
                if cross_time is not None:
                    min_required = cross_time
                else:
                    min_required = 30
            else:
                min_required = 15
            if gap < min_required:
                issues.append(RouteAnalysisIssue(
                    severity="error", dimension="transfer_time",
                    message=f"Transfer gap at leg {i+1}->{i+2} ({arr_station}->{dep_station}) is only {int(gap)}min, "
                            f"minimum {min_required}min required for cross-station transfer",
                ))
                is_valid = False
            elif gap > 180:
                issues.append(RouteAnalysisIssue(
                    severity="warning", dimension="transfer_time",
                    message=f"Transfer gap at leg {i+1}->{i+2} is {int(gap)}min, exceeds 3 hours",
                ))
        except (ValueError, KeyError):
            pass

    train_no = legs[0].get("train_no", "")
    is_estimated = option.get("is_estimated", False)
    if train_no.startswith("G_UNKNOWN"):
        if is_estimated:
            issues.append(RouteAnalysisIssue(
                severity="warning", dimension="train_number",
                message=f"Placeholder train number '{train_no}' for {origin_city}->{destination_city} - "
                        f"no route data available, using distance-based estimate. Integrate 12306/Ctrip API for real data.",
            ))
            warnings.append("Route data is estimated, not from real schedules. Verify before booking.")
        else:
            issues.append(RouteAnalysisIssue(
                severity="error", dimension="train_number",
                message=f"Placeholder train number '{train_no}' - no real route data available for {origin_city}->{destination_city}",
            ))
            is_valid = False
    elif is_estimated:
        warnings.append(f"Route {origin_city}->{destination_city} uses distance-based estimation (not real schedule data). "
                        "Times and prices are approximate. Integrate 12306/Ctrip API for accuracy.")

    reliability = "low"
    if data_source == "api":
        reliability = "medium"
    if data_source == "mock" and not any(i.severity == "error" for i in issues):
        reliability = "low"
    if data_source == "api" and not any(i.severity == "error" for i in issues):
        reliability = "high"

    return RouteAnalysisResult(
        is_valid=is_valid,
        data_source=data_source,
        data_source_reliability=reliability,
        issues=issues,
        warnings=warnings,
    )


def analyze_plan(plans: List[dict], travelers: List[dict], destination: str, data_source: str = "mock") -> RouteAnalysisResult:
    all_issues: List[RouteAnalysisIssue] = []
    all_warnings: List[str] = []
    overall_valid = True

    if data_source == "mock":
        all_warnings.append("[WARNING] Using mock data - not real-time. Integrate 12306/Ctrip API for production use.")
    elif data_source in ("12306", "ctrip"):
        all_warnings.append(f"Data source: {data_source}. Verify results against official channels before booking.")

    origin_map = {t.get("name", ""): t.get("origin", "") for t in travelers}

    for plan in plans:
        options = plan.get("options", [])
        for opt in options:
            traveler_name = opt.get("traveler", "")
            origin_city = origin_map.get(traveler_name, "")
            if not origin_city:
                continue
            result = analyze_route_option(opt, origin_city, destination, data_source)
            if not result.is_valid:
                overall_valid = False
            all_issues.extend(result.issues)
            all_warnings.extend(result.warnings)

        same_train_travelers = []
        partial_same_train_travelers = []
        for opt in options:
            strategy = opt.get("strategy", "")
            if strategy == "same-train":
                same_train_travelers.append(opt)
            elif strategy == "partial-same-train":
                partial_same_train_travelers.append(opt)

        if len(same_train_travelers) >= 2:
            train_nos_per_traveler = []
            for opt in same_train_travelers:
                train_nos = {leg.get("train_no") for leg in opt.get("legs", [])}
                train_nos_per_traveler.append(train_nos)
            if train_nos_per_traveler:
                common = set.intersection(*train_nos_per_traveler)
                if not common:
                    all_issues.append(RouteAnalysisIssue(
                        severity="error",
                        dimension="same_train_consistency",
                        message=f"same-train travelers share no common train number. "
                                f"Train sets: {', '.join(str(s) for s in train_nos_per_traveler)}",
                    ))
                    overall_valid = False

        if len(partial_same_train_travelers) >= 2:
            second_leg_nos = []
            for opt in partial_same_train_travelers:
                legs = opt.get("legs", [])
                if len(legs) >= 2:
                    second_leg_nos.append(legs[-1].get("train_no", ""))
            if second_leg_nos and len(set(second_leg_nos)) == 1:
                pass
            elif second_leg_nos and len(set(second_leg_nos)) > 1:
                all_warnings.append(
                    "partial-same-train travelers have different second-leg train numbers. "
                    "They may not actually share a train after transfer."
                )

        arrival_times = []
        for opt in options:
            arr_str = opt.get("arrival_time", "")
            if arr_str:
                try:
                    arrival_times.append(datetime.strptime(arr_str, DT_FMT))
                except ValueError:
                    pass
        if len(arrival_times) >= 2:
            spread = (max(arrival_times) - min(arrival_times)).total_seconds() / 60
            if spread == 0:
                pass
            elif spread <= 15:
                pass
            elif spread > 120:
                all_warnings.append(
                    f"Arrival time spread is {int(spread)} minutes across travelers. "
                    "Consider tightening max_arrival_time_diff_minutes if closer sync is needed."
                )

    unique_warnings = list(dict.fromkeys(all_warnings))

    reliability = "low"
    if data_source == "mock" and not any(i.severity == "error" for i in all_issues):
        reliability = "low"
    elif data_source in ("12306", "ctrip") and not any(i.severity == "error" for i in all_issues):
        reliability = "high"
    elif data_source in ("12306", "ctrip"):
        reliability = "medium"
    elif data_source == "api" and not any(i.severity == "error" for i in all_issues):
        reliability = "high"
    elif data_source == "api":
        reliability = "medium"

    return RouteAnalysisResult(
        is_valid=overall_valid,
        data_source=data_source,
        data_source_reliability=reliability,
        issues=all_issues,
        warnings=unique_warnings,
    )
