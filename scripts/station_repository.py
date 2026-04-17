import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("trip_planner.station_repo")

_DATA_DIR = Path(__file__).parent.parent / "assets"
_STATION_DATA_FILE = _DATA_DIR / "station_data.json"

_cache: Optional[dict] = None


def _load_station_data() -> dict:
    global _cache
    if _cache is not None:
        return _cache

    override_path = os.getenv("TRIP_PLANNER_STATION_DATA", "")
    data_file = Path(override_path) if override_path else _STATION_DATA_FILE

    if not data_file.exists():
        logger.warning("Station data file not found at %s. Using built-in fallback.", data_file)
        _cache = {"version": "fallback", "cities": {}, "cross_station_transfer_times": {}}
        return _cache

    try:
        raw = data_file.read_text(encoding="utf-8")
        _cache = json.loads(raw)
        city_count = len(_cache.get("cities", {}))
        logger.info("Loaded station data v%s: %d cities from %s", _cache.get("version", "?"), city_count, data_file)
        return _cache
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load station data: %s", e)
        _cache = {"version": "fallback", "cities": {}, "cross_station_transfer_times": {}}
        return _cache


def reload_station_data() -> None:
    global _cache
    _cache = None
    _load_station_data()


def get_all_cities() -> Dict[str, dict]:
    data = _load_station_data()
    return data.get("cities", {})


def get_city_info(city_name: str) -> Optional[dict]:
    cities = get_all_cities()
    if city_name in cities:
        return cities[city_name]
    for city_key, city_data in cities.items():
        cn_names = city_data.get("cn_names", [])
        if city_name in cn_names:
            return city_data
    return None


def get_stations_for_city(city_name: str) -> List[str]:
    info = get_city_info(city_name)
    if info:
        return info.get("stations", [city_name])
    return [city_name]


def get_primary_station(city_name: str) -> str:
    info = get_city_info(city_name)
    if info:
        return info.get("primary_hsr_station", city_name)
    return city_name


def resolve_city_name(raw: str) -> str:
    if not raw:
        return raw
    cities = get_all_cities()
    if raw in cities:
        return raw
    raw_lower = raw.lower()
    for city_key, city_data in cities.items():
        if raw_lower == city_key.lower():
            return city_key
        cn_names = city_data.get("cn_names", [])
        if raw in cn_names:
            return city_key
    return raw


def validate_station_for_city(station_name: str, city_name: str) -> Tuple[bool, str]:
    resolved_city = resolve_city_name(city_name)
    info = get_city_info(resolved_city)
    if not info:
        return False, f"Unknown city: {city_name}. Cannot verify station assignment. Use trip_planner_list_cities to check supported cities."
    city_stations = info.get("stations", [])
    if station_name in city_stations:
        return True, ""
    city_base = resolved_city.split("_")[0]
    if city_base in station_name:
        return True, f"Station {station_name} not in verified list for {city_name}, but contains city name. Proceed with caution."
    return False, f"Station {station_name} does not belong to {city_name}. Expected one of: {', '.join(city_stations)}"


def is_known_station(station_name: str) -> bool:
    cities = get_all_cities()
    for city_data in cities.values():
        if station_name in city_data.get("stations", []):
            return True
    return False


def get_all_known_stations() -> set:
    stations = set()
    cities = get_all_cities()
    for city_data in cities.values():
        stations.update(city_data.get("stations", []))
    return stations


def get_cross_station_transfer_minutes(station_a: str, station_b: str) -> Optional[int]:
    data = _load_station_data()
    transfer_times = data.get("cross_station_transfer_times", {})
    key1 = f"{station_a}|{station_b}"
    key2 = f"{station_b}|{station_a}"
    if key1 in transfer_times:
        return transfer_times[key1]
    if key2 in transfer_times:
        return transfer_times[key2]
    return None


def get_cn_name_map() -> Dict[str, str]:
    result = {}
    cities = get_all_cities()
    for city_key, city_data in cities.items():
        for cn_name in city_data.get("cn_names", []):
            result[cn_name] = city_key
    return result


def get_city_list_summary() -> List[Dict[str, str]]:
    cities = get_all_cities()
    result = []
    for city_key, city_data in sorted(cities.items()):
        cn_names = city_data.get("cn_names", [])
        primary = city_data.get("primary_hsr_station", "")
        result.append({
            "city": city_key,
            "cn_name": cn_names[0] if cn_names else "",
            "primary_station": primary,
            "station_count": len(city_data.get("stations", [])),
        })
    return result


def validate_city_has_hsr(city_name: str) -> Tuple[bool, str]:
    resolved = resolve_city_name(city_name)
    info = get_city_info(resolved)
    if info:
        return True, ""
    available = [c for c in get_all_cities().keys()][:15]
    return False, f"City '{city_name}' is not in the verified HSR station database. Available cities include: {', '.join(available)}... (and more). Use trip_planner_list_cities to see all supported cities."
