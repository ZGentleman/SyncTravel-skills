import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple

from models import (
    DT_FMT,
    MIN_TRANSFER_MINUTES,
    MAX_TRANSFER_MINUTES,
    CandidateOption,
    StrategyType,
    get_cross_station_transfer_minutes,
)
from station_repository import get_primary_station as get_primary_hsr_station

logger = logging.getLogger("trip_planner.strategy")

MAJOR_HUB_CITIES = [
    "Wuhan", "Zhengzhou", "Changsha", "Shijiazhuang", "Nanjing",
    "Hefei", "Jinan", "Xuzhou", "Xi'an", "Chengdu",
    "Nanchang", "Hangzhou", "Shanghai", "Guangzhou", "Beijing",
    "Shenyang", "Harbin", "Chongqing", "Guiyang", "Kunming",
    "Shenzhen", "Tianjin", "Taiyuan", "Lanzhou", "Fuzhou",
    "Nanning", "Hohhot", "Urumqi", "Xining", "Yinchuan",
    "Qingdao", "Dalian", "Suzhou", "Ningbo", "Xiamen",
]


def _parse_dt(value: str) -> datetime:
    return datetime.strptime(value, DT_FMT)


def _safe_time_diff_minutes(earlier: datetime, later: datetime) -> float:
    diff = (later - earlier).total_seconds()
    if diff < 0:
        diff += 86400
    return diff / 60


def detect_same_train_options(
    traveler_raw_options: Dict[str, List[dict]],
    all_travelers: List[dict],
) -> Dict[str, List[dict]]:
    """
    Detect same-train opportunities from raw direct train data.

    A same-train opportunity exists when two or more travelers can board
    the same train at different stations. We detect this by matching
    train numbers across travelers' options.

    For real API data, this works because the same train number (e.g. G80)
    will appear in queries for different origin-destination pairs if the
    train passes through both origins.
    """
    train_to_travelers: Dict[str, List[Tuple[str, dict]]] = defaultdict(list)

    for traveler_name, options in traveler_raw_options.items():
        for option in options:
            for leg in option.get("legs", []):
                train_no = leg.get("train_no", "")
                if train_no and not train_no.startswith("G_UNKNOWN") and not train_no.startswith("EST"):
                    train_to_travelers[train_no].append((traveler_name, option))

    shared_trains: Dict[str, Set[str]] = {}
    for train_no, traveler_list in train_to_travelers.items():
        if len(traveler_list) >= 2:
            names = {name for name, _ in traveler_list}
            if len(names) >= 2:
                shared_trains[train_no] = names

    if not shared_trains:
        return {}

    result: Dict[str, List[dict]] = defaultdict(list)
    for train_no, traveler_names in shared_trains.items():
        for traveler_name in traveler_names:
            for option in traveler_raw_options.get(traveler_name, []):
                for leg in option.get("legs", []):
                    if leg.get("train_no") == train_no:
                        same_train_option = _convert_to_strategy_option(
                            option, "same-train", shared_train_ratio=1.0
                        )
                        result[traveler_name].append(same_train_option)
                        break

    return dict(result)


def detect_partial_same_train_options(
    traveler_raw_options: Dict[str, List[dict]],
    transfer_options: Dict[str, List[dict]],
    all_travelers: List[dict],
) -> Dict[str, List[dict]]:
    """
    Detect partial-same-train opportunities where travelers share
    the final leg after transferring at a hub.

    This checks if the second leg of a transfer option matches
    a direct train of another traveler.
    """
    result: Dict[str, List[dict]] = defaultdict(list)

    direct_train_nos: Dict[str, Set[str]] = {}
    for traveler_name, options in traveler_raw_options.items():
        direct_train_nos[traveler_name] = set()
        for option in options:
            for leg in option.get("legs", []):
                train_no = leg.get("train_no", "")
                if train_no and not train_no.startswith("G_UNKNOWN") and not train_no.startswith("EST"):
                    direct_train_nos[traveler_name].add(train_no)

    for traveler_name, options in transfer_options.items():
        for option in options:
            if option.get("total_transfers", 0) < 1:
                continue
            legs = option.get("legs", [])
            if len(legs) < 2:
                continue

            second_leg_train = legs[-1].get("train_no", "")
            if not second_leg_train:
                continue

            is_shared = False
            for other_name, other_trains in direct_train_nos.items():
                if other_name == traveler_name:
                    continue
                if second_leg_train in other_trains:
                    is_shared = True
                    break

            if not is_shared:
                for other_name, other_options in transfer_options.items():
                    if other_name == traveler_name:
                        continue
                    for other_opt in other_options:
                        other_legs = other_opt.get("legs", [])
                        if len(other_legs) >= 2 and other_legs[-1].get("train_no") == second_leg_train:
                            is_shared = True
                            break
                    if is_shared:
                        break

            if is_shared and option.get("strategy") == "transfer-merge":
                partial_option = _convert_to_strategy_option(
                    option, "partial-same-train", shared_train_ratio=0.7
                )
                result[traveler_name].append(partial_option)
            elif is_shared and option.get("strategy") != "partial-same-train":
                result[traveler_name].append(option)

    return dict(result)


def generate_transfer_merge_options(
    traveler_name: str,
    origin: str,
    destination: str,
    date: str,
    dep0: datetime,
    dep1: datetime,
    hub_data: Dict[str, Dict[str, List[dict]]],
    all_traveler_direct_options: Dict[str, List[dict]],
    all_travelers: List[dict],
) -> List[dict]:
    """
    Generate transfer-merge options for a traveler using hub query data.

    hub_data structure:
    {
        "hub_city_name": {
            "origin_to_hub": [raw train options from origin to hub],
            "hub_to_dest": [raw train options from hub to destination]
        }
    }

    This function combines first-leg and second-leg trains at each hub,
    validates transfer times, and generates transfer-merge options.
    """
    options = []

    for hub_city, data in hub_data.items():
        if hub_city == origin or hub_city == destination:
            continue

        origin_to_hub_trains = data.get("origin_to_hub", [])
        hub_to_dest_trains = data.get("hub_to_dest", [])

        if not origin_to_hub_trains or not hub_to_dest_trains:
            continue

        for first_leg_raw in origin_to_hub_trains:
            first_legs = first_leg_raw.get("legs", [])
            if not first_legs:
                continue

            first_dep_time_str = first_legs[0].get("dep_time", "")
            if not first_dep_time_str:
                continue

            try:
                first_dep_time = _parse_dt(first_dep_time_str)
            except ValueError:
                continue

            if first_dep_time < dep0 or first_dep_time > dep1:
                continue

            first_arr_time_str = first_legs[-1].get("arr_time", "")
            if not first_arr_time_str:
                continue

            try:
                first_arr_time = _parse_dt(first_arr_time_str)
            except ValueError:
                continue

            for second_leg_raw in hub_to_dest_trains:
                second_legs = second_leg_raw.get("legs", [])
                if not second_legs:
                    continue

                second_dep_time_str = second_legs[0].get("dep_time", "")
                if not second_dep_time_str:
                    continue

                try:
                    second_dep_time = _parse_dt(second_dep_time_str)
                except ValueError:
                    continue

                transfer_gap = _safe_time_diff_minutes(first_arr_time, second_dep_time)

                arr_station_first = first_legs[-1].get("arr_station", "")
                dep_station_second = second_legs[0].get("dep_station", "")

                if arr_station_first and dep_station_second and arr_station_first != dep_station_second:
                    cross_time = get_cross_station_transfer_minutes(arr_station_first, dep_station_second)
                    min_required = cross_time if cross_time is not None else MIN_TRANSFER_MINUTES + 15
                else:
                    min_required = MIN_TRANSFER_MINUTES

                if transfer_gap < min_required or transfer_gap > MAX_TRANSFER_MINUTES:
                    continue

                second_arr_time_str = second_legs[-1].get("arr_time", "")
                if not second_arr_time_str:
                    continue

                try:
                    final_arr_time = _parse_dt(second_arr_time_str)
                except ValueError:
                    continue

                total_minutes = int(_safe_time_diff_minutes(first_dep_time, final_arr_time))
                if total_minutes <= 0 or total_minutes > 1440:
                    continue

                first_price = first_leg_raw.get("total_price", 0)
                second_price = second_leg_raw.get("total_price", 0)
                total_price = round(float(first_price) + float(second_price), 2)

                second_leg_train_no = second_legs[0].get("train_no", "")
                is_shared_final = False
                for other_name, other_opts in all_traveler_direct_options.items():
                    if other_name == traveler_name:
                        continue
                    for other_opt in other_opts:
                        for other_leg in other_opt.get("legs", []):
                            if other_leg.get("train_no") == second_leg_train_no:
                                is_shared_final = True
                                break
                        if is_shared_final:
                            break
                    if is_shared_final:
                        break

                strategy = "partial-same-train" if is_shared_final else "transfer-merge"
                shared_ratio = 0.7 if is_shared_final else 0.5

                combined_legs = []
                for leg in first_legs:
                    combined_legs.append({
                        "train_no": leg.get("train_no", ""),
                        "dep_station": leg.get("dep_station", ""),
                        "arr_station": leg.get("arr_station", ""),
                        "dep_time": leg.get("dep_time", ""),
                        "arr_time": leg.get("arr_time", ""),
                    })
                for leg in second_legs:
                    combined_legs.append({
                        "train_no": leg.get("train_no", ""),
                        "dep_station": leg.get("dep_station", ""),
                        "arr_station": leg.get("arr_station", ""),
                        "dep_time": leg.get("dep_time", ""),
                        "arr_time": leg.get("arr_time", ""),
                    })

                option = {
                    "strategy": strategy,
                    "arrival_time": final_arr_time.strftime(DT_FMT),
                    "total_minutes": total_minutes,
                    "total_price": total_price,
                    "total_transfers": 1,
                    "train_type": "HSR",
                    "seat_type": first_leg_raw.get("seat_type", "second-class"),
                    "merge_hub": hub_city,
                    "shared_train_ratio": shared_ratio,
                    "is_estimated": first_leg_raw.get("is_estimated", False) and second_leg_raw.get("is_estimated", False),
                    "data_source": first_leg_raw.get("data_source", ""),
                    "legs": combined_legs,
                }

                try:
                    CandidateOption.model_validate(option)
                    options.append(option)
                except Exception as e:
                    logger.debug("Skipping invalid transfer option: %s", e)
                    continue

    return options


def generate_synchronized_arrival_options(
    traveler_raw_options: Dict[str, List[dict]],
) -> Dict[str, List[dict]]:
    """
    Mark direct train options as synchronized-arrival strategy.
    These are independent trains where travelers arrive at similar times.
    """
    result: Dict[str, List[dict]] = {}
    for traveler_name, options in traveler_raw_options.items():
        sync_options = []
        for option in options:
            if option.get("total_transfers", 0) == 0:
                sync_option = _convert_to_strategy_option(
                    option, "synchronized-arrival", shared_train_ratio=0.0
                )
                sync_options.append(sync_option)
        result[traveler_name] = sync_options
    return result


def generate_all_strategies(
    direct_options: Dict[str, List[dict]],
    hub_options: Dict[str, Dict[str, List[dict]]],
    all_travelers: List[dict],
    destination: str,
    date: str,
    dep0_map: Dict[str, datetime],
    dep1_map: Dict[str, datetime],
) -> Dict[str, List[dict]]:
    """
    Master function: generate all strategy-enriched options from raw train data.

    Pipeline:
    1. Direct trains -> synchronized-arrival (baseline)
    2. Direct trains -> same-train detection
    3. Hub data -> transfer-merge / partial-same-train
    4. Combine and deduplicate

    Args:
        direct_options: Raw direct train options per traveler from any data source
        hub_options: Hub query data per traveler:
            {traveler_name: {hub_city: {"origin_to_hub": [...], "hub_to_dest": [...]}}}
        all_travelers: List of traveler dicts with name, origin, etc.
        destination: Common destination city
        date: Travel date string
        dep0_map: Earliest departure per traveler
        dep1_map: Latest departure per traveler

    Returns:
        Strategy-enriched options per traveler
    """
    result: Dict[str, List[dict]] = defaultdict(list)

    sync_options = generate_synchronized_arrival_options(direct_options)
    for name, opts in sync_options.items():
        result[name].extend(opts)

    same_train_options = detect_same_train_options(direct_options, all_travelers)
    for name, opts in same_train_options.items():
        result[name].extend(opts)

    all_transfer_options: Dict[str, List[dict]] = defaultdict(list)
    for traveler in all_travelers:
        name = traveler["name"]
        origin = traveler.get("origin", "")
        dep0 = dep0_map.get(name)
        dep1 = dep1_map.get(name)

        if not dep0 or not dep1:
            continue

        traveler_hub_data = hub_options.get(name, {})
        if not traveler_hub_data:
            continue

        transfer_opts = generate_transfer_merge_options(
            traveler_name=name,
            origin=origin,
            destination=destination,
            date=date,
            dep0=dep0,
            dep1=dep1,
            hub_data=traveler_hub_data,
            all_traveler_direct_options=direct_options,
            all_travelers=all_travelers,
        )
        all_transfer_options[name].extend(transfer_opts)

    partial_options = detect_partial_same_train_options(
        direct_options, dict(all_transfer_options), all_travelers
    )
    for name, opts in partial_options.items():
        all_transfer_options[name].extend(opts)

    for name, opts in all_transfer_options.items():
        result[name].extend(opts)

    result = _deduplicate_options(dict(result))

    for name in result:
        if not result[name]:
            logger.warning("No strategy options generated for traveler %s", name)

    return dict(result)


def _convert_to_strategy_option(
    option: dict, strategy: str, shared_train_ratio: float = 0.0
) -> dict:
    """Convert a raw option to a strategy-specific option."""
    new_option = dict(option)
    new_option["strategy"] = strategy
    new_option["shared_train_ratio"] = shared_train_ratio
    return new_option


def _deduplicate_options(
    options: Dict[str, List[dict]],
) -> Dict[str, List[dict]]:
    """Remove duplicate options based on train numbers and strategy."""
    result: Dict[str, List[dict]] = {}

    for name, opts in options.items():
        seen: Set[Tuple] = set()
        unique = []
        for opt in opts:
            train_nos = tuple(
                leg.get("train_no", "") for leg in opt.get("legs", [])
            )
            key = (train_nos, opt.get("strategy", ""))
            if key not in seen:
                seen.add(key)
                unique.append(opt)
        result[name] = unique

    return result


def get_relevant_hubs(
    origin: str, destination: str, max_hubs: int = 5
) -> List[str]:
    """
    Determine relevant transfer hub cities for a given origin-destination pair.

    This uses geographic knowledge of major railway hubs, NOT hard-coded routes.
    The actual train schedules are still queried from real APIs.

    The hub list is a reasonable static configuration - major railway junction
    cities don't change frequently. This is analogous to how navigation apps
    maintain a list of major airports.
    """
    from provider_layer import _CITY_COORDINATES, _estimate_distance_km

    origin_dist = _estimate_distance_km(origin, destination)
    if not origin_dist or origin_dist < 100:
        return []

    scored_hubs = []
    for hub in MAJOR_HUB_CITIES:
        if hub == origin or hub == destination:
            continue

        origin_to_hub = _estimate_distance_km(origin, hub)
        hub_to_dest = _estimate_distance_km(hub, destination)

        if not origin_to_hub or not hub_to_dest:
            continue

        if origin_to_hub < 50 or hub_to_dest < 50:
            continue

        if origin_to_hub >= origin_dist or hub_to_dest >= origin_dist:
            continue

        detour_ratio = (origin_to_hub + hub_to_dest) / origin_dist
        if detour_ratio > 2.0:
            continue

        scored_hubs.append((hub, detour_ratio, origin_to_hub + hub_to_dest))

    scored_hubs.sort(key=lambda x: (x[1], x[2]))

    return [hub for hub, _, _ in scored_hubs[:max_hubs]]
