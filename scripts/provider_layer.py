import hashlib
import json
import logging
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from models import (
    DT_FMT,
    CandidateOption,
    StrategyType,
    TripPlannerInput,
    city_to_stations,
    station_belongs_to_city,
)
from station_repository import get_primary_station as get_primary_hsr_station

try:
    import requests
except Exception:
    requests = None

logger = logging.getLogger("trip_planner.provider")


class ProviderError(RuntimeError):
    def __init__(self, message: str):
        self.user_message = message
        super().__init__(message)


_CITY_COORDINATES: Dict[str, Tuple[float, float]] = {
    "Beijing": (39.90, 116.40), "Shanghai": (31.23, 121.47), "Guangzhou": (23.13, 113.26),
    "Shenzhen": (22.54, 114.06), "Wuhan": (30.59, 114.31), "Zhengzhou": (34.75, 113.65),
    "Changsha": (28.23, 112.94), "Nanjing": (32.06, 118.80), "Chengdu": (30.57, 104.07),
    "Hangzhou": (30.27, 120.15), "Shijiazhuang": (38.04, 114.51), "Xi'an": (34.26, 108.94),
    "Jinan": (36.65, 117.00), "Hefei": (31.82, 117.23), "Nanchang": (28.68, 115.86),
    "Fuzhou": (26.07, 119.30), "Kunming": (25.04, 102.68), "Guiyang": (26.65, 106.63),
    "Chongqing": (29.56, 106.55), "Tianjin": (39.13, 117.20), "Suzhou": (31.30, 120.62),
    "Qingdao": (36.07, 120.38), "Dalian": (38.91, 121.60), "Shenyang": (41.80, 123.43),
    "Harbin": (45.75, 126.65), "Changchun": (43.88, 125.32), "Taiyuan": (37.87, 112.55),
    "Lanzhou": (36.06, 103.83), "Xuzhou": (34.26, 117.18), "Wuxi": (31.49, 120.31),
    "Ningbo": (29.87, 121.55), "Wenzhou": (28.00, 120.67), "Xiamen": (24.48, 118.09),
    "Nanning": (22.82, 108.37), "Haikou": (20.04, 110.35), "Sanya": (18.25, 109.50),
    "Urumqi": (43.83, 87.62), "Lhasa": (29.65, 91.10), "Hohhot": (40.84, 111.75),
    "Yinchuan": (38.49, 106.23), "Xining": (36.62, 101.78),
    "Baotou": (40.66, 109.84), "Dongguan": (23.04, 113.75), "Foshan": (23.02, 113.12),
    "Zhuhai": (22.27, 113.58), "Zhongshan": (22.52, 113.39), "Huizhou": (23.11, 114.42),
    "Ganzhou": (25.83, 114.93), "Jiujiang": (29.71, 116.00), "Yichang": (30.69, 111.29),
    "Xiangyang": (32.04, 112.14), "Luoyang": (34.62, 112.45), "Kaifeng": (34.80, 114.35),
    "Weifang": (36.71, 119.16), "Yantai": (37.46, 121.45), "Weihai": (37.51, 122.12),
    "Linyi": (35.10, 118.36), "Jining": (35.41, 116.59), "Taian": (36.20, 117.09),
    "Dezhou": (37.43, 116.36), "Liaocheng": (36.45, 115.98), "Zibo": (36.81, 118.05),
    "Zaozhuang": (34.81, 117.32), "Rizhao": (35.42, 119.53), "Binzhou": (37.38, 117.97),
    "Mianyang": (31.47, 104.73), "Deyang": (31.13, 104.40), "Leshan": (29.55, 103.77),
    "Nanchong": (30.84, 106.07), "Yibin": (28.77, 104.62), "Luzhou": (28.87, 105.44),
    "Zunyi": (27.73, 106.93), "Qujing": (25.49, 103.80), "Yuxi": (24.35, 102.54),
    "Baoshan": (25.11, 99.17), "Liuzhou": (24.33, 109.41), "Guilin": (25.27, 110.29),
    "Wuzhou": (23.48, 111.28), "Beihai": (21.48, 109.12), "Yulin": (22.65, 110.15),
    "Changzhou": (31.77, 119.95), "Nantong": (31.98, 120.86), "Yangzhou": (32.39, 119.42),
    "Zhenjiang": (32.19, 119.45), "Yancheng": (33.35, 120.16), "Taizhou": (32.46, 119.91),
    "Huai'an": (33.55, 119.02), "Lianyungang": (34.60, 119.22), "Suqian": (33.96, 118.28),
    "Jiaxing": (30.75, 120.76), "Huzhou": (30.87, 120.09), "Shaoxing": (30.00, 120.58),
    "Jinhua": (29.08, 119.65), "Quzhou": (28.94, 118.87), "Zhoushan": (29.95, 122.11),
    "Taizhou_ZJ": (28.66, 121.42), "Lishui": (28.45, 119.92), "Putian": (25.43, 119.01),
    "Quanzhou": (24.87, 118.68), "Zhangzhou": (24.51, 117.65), "Longyan": (25.08, 117.01),
    "Sanming": (26.27, 117.64), "Nanping": (26.64, 118.18), "Ningde": (26.66, 119.53),
    "Pingxiang": (27.62, 114.15), "Jingdezhen": (29.29, 117.21), "Yingtan": (28.26, 117.07),
    "Ji'an": (27.11, 114.99), "Shangrao": (28.45, 117.97), "Fuyang": (32.89, 115.81),
    "Bengbu": (32.92, 117.39), "Chuzhou": (32.30, 118.32),
    "Anqing": (30.51, 117.05), "Ma'anshan": (31.67, 118.51), "Chizhou": (30.66, 117.49),
    "Tongling": (30.65, 117.81), "Xuancheng": (30.95, 118.76), "Lu'an": (31.73, 116.50),
    "Hengyang": (26.89, 112.57), "Yueyang": (29.36, 113.13), "Changde": (29.03, 111.68),
    "Yiyang": (28.55, 112.36), "Chenzhou": (25.77, 113.01), "Shaoyang": (27.24, 111.47),
    "Loudi": (27.70, 111.99), "Huaihua": (27.55, 110.00), "Yongzhou": (26.42, 111.61),
    "Zhangjiajie": (29.12, 110.48), "Xiangtan": (27.83, 112.94), "Zhuzhou": (27.83, 113.13),
    "Huangshi": (30.20, 115.04), "Shiyan": (32.65, 110.80), "Jingzhou": (30.33, 112.24),
    "Xiaogan": (30.92, 113.92), "Huanggang": (30.45, 114.87), "Xianning": (29.84, 114.32),
    "Suizhou": (31.69, 113.38), "Enshi": (30.27, 109.49), "Xiantao": (30.36, 113.44),
    "Qianjiang": (30.40, 112.90), "Tianmen": (30.65, 113.17),
    "Jiamusi": (46.80, 130.37), "Qiqihar": (47.35, 123.92), "Mudanjiang": (44.55, 129.63),
    "Jilin": (43.84, 126.55), "Siping": (43.17, 124.38), "Tonghua": (41.73, 125.94),
    "Songyuan": (45.14, 124.83), "Baicheng": (45.62, 122.84), "Anshan": (41.11, 122.99),
    "Fushun": (41.88, 123.96), "Benxi": (41.30, 123.76), "Dandong": (40.00, 124.35),
    "Jinzhou": (41.10, 121.13), "Yingkou": (40.67, 122.23), "Fuxin": (42.01, 121.67),
    "Liaoyang": (41.27, 123.17), "Panjin": (41.12, 122.07), "Tieling": (42.29, 123.84),
    "Chaoyang": (41.57, 120.45), "Huludao": (40.76, 120.86),
    "Baoding": (38.87, 115.46), "Handan": (36.63, 114.54), "Qinhuangdao": (39.94, 119.60),
    "Zhangjiakou": (40.77, 114.88), "Chengde": (40.95, 117.96), "Langfang": (39.52, 116.68),
    "Cangzhou": (38.30, 116.84), "Hengshui": (37.73, 115.68), "Xingtai": (37.07, 114.50),
    "Tangshan": (39.63, 118.18),
    "Wuhu": (31.33, 118.38), "Huaibei": (33.97, 116.79), "Bozhou": (33.87, 115.78),
}


def _estimate_distance_km(origin: str, destination: str) -> Optional[int]:
    c1 = _CITY_COORDINATES.get(origin)
    c2 = _CITY_COORDINATES.get(destination)
    if not c1 or not c2:
        return None
    lat1, lon1 = math.radians(c1[0]), math.radians(c1[1])
    lat2, lon2 = math.radians(c2[0]), math.radians(c2[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    km = c * 6371
    return int(km * 1.25)


def _generate_estimated_route(
    origin: str,
    destination: str,
    dep0: datetime,
    dep1: datetime,
    date: str,
) -> List[dict]:
    dist = _estimate_distance_km(origin, destination)
    if not dist or dist < 50:
        return []
    speed_kmh = 280
    duration_hours = round(dist / speed_kmh, 1)
    if duration_hours < 0.5:
        duration_hours = 0.5
    price_per_km = 0.46
    price = round(dist * price_per_km, 0)
    dep_station = get_primary_hsr_station(origin)
    arr_station = get_primary_hsr_station(destination)
    options = []
    train_counter = 1
    for hour in range(7, 20):
        for minute_offset in [0, 30]:
            candidate = datetime.strptime(f"{date} {hour:02d}:{minute_offset:02d}", DT_FMT)
            if candidate < dep0 or candidate > dep1:
                continue
            dep_time = candidate
            arr_time = dep_time + timedelta(hours=duration_hours)
            train_no = f"EST_{origin}_{destination}_{train_counter:03d}"
            train_counter += 1
            option = {
                "strategy": "synchronized-arrival",
                "arrival_time": arr_time.strftime(DT_FMT),
                "total_minutes": int((arr_time - dep_time).total_seconds() // 60),
                "total_price": price,
                "total_transfers": 0,
                "train_type": "HSR",
                "seat_type": "second-class",
                "shared_train_ratio": 0.0,
                "is_estimated": True,
                "data_source": "distance-estimation",
                "legs": [
                    {
                        "train_no": train_no,
                        "dep_station": dep_station,
                        "arr_station": arr_station,
                        "dep_time": dep_time.strftime(DT_FMT),
                        "arr_time": arr_time.strftime(DT_FMT),
                    }
                ],
            }
            try:
                CandidateOption.model_validate(option)
            except Exception:
                continue
            options.append(option)
            if len(options) >= 6:
                break
        if len(options) >= 6:
            break
    return options


class FileTTLCache:
    def __init__(self, cache_dir: str, ttl_seconds: int = 180):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def get(self, key: str) -> Optional[dict]:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - float(payload["saved_at"]) > self.ttl_seconds:
                return None
            return payload["value"]
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            return None

    def set(self, key: str, value: dict) -> None:
        path = self._path(key)
        path.write_text(
            json.dumps({"saved_at": time.time(), "value": value}, ensure_ascii=False),
            encoding="utf-8",
        )


def with_retry(func, retries: int = 3, base_sleep_seconds: float = 0.4):
    last_err = None
    for i in range(retries):
        try:
            return func()
        except Exception as err:
            last_err = err
            if i == retries - 1:
                break
            time.sleep(base_sleep_seconds * (2**i))
    raise ProviderError(f"Provider request failed after {retries} retries")


class BaseCandidateProvider:
    def get_candidate_options(self, payload: dict) -> Dict[str, List[dict]]:
        raise NotImplementedError

    def get_hub_data(
        self,
        origin: str,
        destination: str,
        date: str,
        hub_cities: List[str],
        dep0: datetime,
        dep1: datetime,
        shared_hub_to_dest_cache: Optional[Dict[str, List[dict]]] = None,
    ) -> Dict[str, Dict[str, List[dict]]]:
        return {}


class EstimationProvider(BaseCandidateProvider):
    """
    Distance-based estimation provider. No hard-coded routes.
    Uses Haversine formula + city coordinates to estimate travel time and price.
    This is the universal fallback that works for ANY city pair in China.

    Data source: distance-estimation (reliability: low)
    For production: use 12306/Ctrip API adapters instead.
    """

    def get_candidate_options(self, payload: dict) -> Dict[str, List[dict]]:
        date = payload.get("date", datetime.now().strftime("%Y-%m-%d"))
        destination = payload.get("destination", "Beijing")
        all_travelers = payload.get("travelers", [])
        result: Dict[str, List[dict]] = {}

        for t in all_travelers:
            name = t["name"]
            origin = t.get("origin", "")
            dep0 = datetime.strptime(t.get("earliest_departure", f"{date} 07:00"), DT_FMT)
            dep1 = datetime.strptime(t.get("latest_departure", f"{date} 15:00"), DT_FMT)

            if origin == destination:
                result[name] = []
                continue

            estimated = _generate_estimated_route(origin, destination, dep0, dep1, date)
            if estimated:
                result[name] = estimated
            else:
                dep_time = dep0
                arr_time = dep_time + timedelta(hours=7)
                dep_station = get_primary_hsr_station(origin)
                arr_station = get_primary_hsr_station(destination)
                option = {
                    "strategy": "synchronized-arrival",
                    "arrival_time": arr_time.strftime(DT_FMT),
                    "total_minutes": int((arr_time - dep_time).total_seconds() // 60),
                    "total_price": 600.0,
                    "total_transfers": 0,
                    "train_type": "HSR",
                    "seat_type": "second-class",
                    "shared_train_ratio": 0.0,
                    "is_estimated": True,
                    "data_source": "distance-estimation",
                    "legs": [
                        {
                            "train_no": "G_UNKNOWN",
                            "dep_station": dep_station,
                            "arr_station": arr_station,
                            "dep_time": dep_time.strftime(DT_FMT),
                            "arr_time": arr_time.strftime(DT_FMT),
                        }
                    ],
                }
                CandidateOption.model_validate(option)
                result[name] = [option]

        return result

    def get_hub_data(
        self,
        origin: str,
        destination: str,
        date: str,
        hub_cities: List[str],
        dep0: datetime,
        dep1: datetime,
        shared_hub_to_dest_cache: Optional[Dict[str, List[dict]]] = None,
    ) -> Dict[str, Dict[str, List[dict]]]:
        hub_data: Dict[str, Dict[str, List[dict]]] = {}
        for hub in hub_cities:
            if hub == origin or hub == destination:
                continue
            o2h_dep0 = dep0
            o2h_dep1 = dep1
            origin_to_hub = _generate_estimated_route(origin, hub, o2h_dep0, o2h_dep1, date)

            hub_dep0 = datetime.strptime(f"{date} 06:00", DT_FMT)
            hub_dep1 = datetime.strptime(f"{date} 23:00", DT_FMT)
            hub_to_dest = _generate_estimated_route(hub, destination, hub_dep0, hub_dep1, date)

            if origin_to_hub or hub_to_dest:
                hub_data[hub] = {
                    "origin_to_hub": origin_to_hub,
                    "hub_to_dest": hub_to_dest,
                }
        return hub_data


class RailwayAPIProvider(BaseCandidateProvider):
    """
    Real railway API provider using 12306/Ctrip adapters.

    Fetches raw direct train data from real APIs.
    Falls back to EstimationProvider if API is unavailable.

    Data source: 12306/ctrip (reliability: high when API is configured)
    """

    def __init__(
        self,
        adapter_provider: str = "12306",
        endpoint: str = "",
        api_key: str = "",
        timeout_seconds: float = 10.0,
        fallback_to_estimation: bool = True,
    ):
        self.adapter_provider = adapter_provider
        self.endpoint = endpoint
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.fallback_to_estimation = fallback_to_estimation
        self._estimation = EstimationProvider()
        self._api_succeeded = False

    def get_candidate_options(self, payload: dict) -> Dict[str, List[dict]]:
        try:
            from railway_api import get_adapter, RailwayAPIError
        except ImportError:
            logger.warning("railway_api module not available, falling back to estimation")
            return self._estimation.get_candidate_options(payload)

        date = payload.get("date", datetime.now().strftime("%Y-%m-%d"))
        destination = payload.get("destination", "Beijing")
        all_travelers = payload.get("travelers", [])
        result: Dict[str, List[dict]] = {}
        any_api_success = False

        def _query_one_traveler(t: dict) -> Tuple[str, List[dict], bool]:
            name = t["name"]
            origin = t.get("origin", "Unknown")
            if origin == destination:
                return name, [], False
            try:
                adapter = get_adapter(
                    self.adapter_provider,
                    endpoint=self.endpoint,
                    api_key=self.api_key,
                    timeout_seconds=self.timeout_seconds,
                )
                trains = adapter.query_trains_for_city_pair(origin, destination, date)
                for train in trains:
                    train["data_source"] = self.adapter_provider
                return name, trains, True
            except RailwayAPIError as e:
                logger.warning("Railway API query failed for %s->%s: %s", origin, destination, e)
                return name, [], False
            except Exception as e:
                logger.error("Unexpected error querying railway API: %s", e)
                return name, [], False

        if len(all_travelers) > 1:
            with ThreadPoolExecutor(max_workers=min(3, len(all_travelers))) as executor:
                futures = [executor.submit(_query_one_traveler, t) for t in all_travelers]
                for future in as_completed(futures, timeout=30):
                    try:
                        name, trains, success = future.result(timeout=15)
                        result[name] = trains
                        if success:
                            any_api_success = True
                    except Exception as e:
                        logger.warning("Traveler query failed: %s", e)
        else:
            for t in all_travelers:
                name, trains, success = _query_one_traveler(t)
                result[name] = trains
                if success:
                    any_api_success = True

        if self.fallback_to_estimation:
            for t in all_travelers:
                name = t["name"]
                origin = t.get("origin", "")
                if origin == destination:
                    continue
                if not result.get(name):
                    logger.info(
                        "Falling back to distance estimation for %s (%s->%s)",
                        name, origin, destination,
                    )
                    dep0 = datetime.strptime(
                        t.get("earliest_departure", f"{date} 07:00"), DT_FMT
                    )
                    dep1 = datetime.strptime(
                        t.get("latest_departure", f"{date} 15:00"), DT_FMT
                    )
                    estimated = _generate_estimated_route(origin, destination, dep0, dep1, date)
                    if estimated:
                        result[name] = estimated

        self._api_succeeded = any_api_success
        return result

    def get_hub_data(
        self,
        origin: str,
        destination: str,
        date: str,
        hub_cities: List[str],
        dep0: datetime,
        dep1: datetime,
        shared_hub_to_dest_cache: Optional[Dict[str, List[dict]]] = None,
    ) -> Dict[str, Dict[str, List[dict]]]:
        try:
            from railway_api import get_adapter, RailwayAPIError
        except ImportError:
            return self._estimation.get_hub_data(
                origin, destination, date, hub_cities, dep0, dep1,
                shared_hub_to_dest_cache=shared_hub_to_dest_cache,
            )

        hub_data: Dict[str, Dict[str, List[dict]]] = {}

        def _query_one_hub(hub: str) -> Tuple[str, Dict[str, List[dict]]]:
            if hub == origin or hub == destination:
                return hub, {}
            origin_to_hub = []
            hub_to_dest = []
            try:
                adapter = get_adapter(
                    self.adapter_provider,
                    endpoint=self.endpoint,
                    api_key=self.api_key,
                    timeout_seconds=self.timeout_seconds,
                )
                o2h_trains = adapter.query_trains_for_city_pair(origin, hub, date)
                if o2h_trains:
                    for t in o2h_trains:
                        t["data_source"] = self.adapter_provider
                    origin_to_hub = [
                        t for t in o2h_trains
                        if _is_within_departure_window(t, dep0, dep1)
                    ]
                hub_dep0 = datetime.strptime(f"{date} 06:00", DT_FMT)
                hub_dep1 = datetime.strptime(f"{date} 23:00", DT_FMT)
                cache_key = f"{hub}->{destination}"
                if shared_hub_to_dest_cache is not None and cache_key in shared_hub_to_dest_cache:
                    hub_to_dest = shared_hub_to_dest_cache[cache_key]
                else:
                    h2d_trains = adapter.query_trains_for_city_pair(hub, destination, date)
                    if h2d_trains:
                        for t in h2d_trains:
                            t["data_source"] = self.adapter_provider
                        hub_to_dest = [
                            t for t in h2d_trains
                            if _is_within_departure_window(t, hub_dep0, hub_dep1)
                        ]
                    if shared_hub_to_dest_cache is not None and hub_to_dest:
                        shared_hub_to_dest_cache[cache_key] = hub_to_dest
            except RailwayAPIError as e:
                logger.debug("Hub query failed for %s->%s via %s: %s", origin, destination, hub, e)
            except Exception as e:
                logger.debug("Hub query error: %s", e)

            if self.fallback_to_estimation:
                if not origin_to_hub:
                    origin_to_hub = _generate_estimated_route(origin, hub, dep0, dep1, date)
                if not hub_to_dest:
                    hub_dep0 = datetime.strptime(f"{date} 06:00", DT_FMT)
                    hub_dep1 = datetime.strptime(f"{date} 23:00", DT_FMT)
                    hub_to_dest = _generate_estimated_route(hub, destination, hub_dep0, hub_dep1, date)

            if origin_to_hub or hub_to_dest:
                return hub, {
                    "origin_to_hub": origin_to_hub,
                    "hub_to_dest": hub_to_dest,
                }
            return hub, {}

        if len(hub_cities) > 1:
            with ThreadPoolExecutor(max_workers=min(3, len(hub_cities))) as executor:
                futures = [executor.submit(_query_one_hub, hub) for hub in hub_cities]
                for future in as_completed(futures, timeout=45):
                    try:
                        hub, data = future.result(timeout=20)
                        if data:
                            hub_data[hub] = data
                    except Exception as e:
                        logger.warning("Hub query failed: %s", e)
        else:
            for hub in hub_cities:
                hub, data = _query_one_hub(hub)
                if data:
                    hub_data[hub] = data

        return hub_data


class APICandidateProvider(BaseCandidateProvider):
    def __init__(self, endpoint: str, timeout_seconds: float = 8.0, api_key: str = ""):
        if requests is None:
            raise ProviderError("requests library is required for API provider")
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.api_key = api_key

    def get_candidate_options(self, payload: dict) -> Dict[str, List[dict]]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        def do_call():
            response = requests.post(
                self.endpoint,
                json=payload,
                timeout=self.timeout_seconds,
                headers=headers,
            )
            response.raise_for_status()
            body = response.json()
            if "candidate_options" not in body:
                raise ProviderError("API response missing candidate_options")
            return body["candidate_options"]

        try:
            result = with_retry(do_call, retries=3, base_sleep_seconds=0.5)
            for name, opts in result.items():
                for opt in opts:
                    opt["data_source"] = "external-api"
            return result
        except ProviderError:
            raise
        except Exception as err:
            logger.error("Provider API call failed: %s", type(err).__name__)
            raise ProviderError("Failed to retrieve candidate options from provider") from err


class MultiProviderFacade(BaseCandidateProvider):
    """
    Industrial-grade multi-provider facade with automatic fallback chain.

    Default chain: 12306-direct -> 12306-mcp -> distance-estimation

    12306-direct: Free, real-time, no API key needed (PRIMARY)
    12306-mcp: Requires running 12306-MCP server (SECONDARY)
    distance-estimation: Haversine-based fallback (TERTIARY)

    This is the recommended provider for production use.
    """

    def __init__(
        self,
        providers: List[str] = None,
        endpoint: str = "",
        api_key: str = "",
        timeout_seconds: float = 10.0,
    ):
        self.provider_names = providers or ["12306-direct", "12306-mcp"]
        self.endpoint = endpoint
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._estimation = EstimationProvider()
        self._active_provider = None

    def get_candidate_options(self, payload: dict) -> Dict[str, List[dict]]:
        for provider_name in self.provider_names:
            try:
                if provider_name in ("12306-direct", "12306-mcp"):
                    provider = RailwayAPIProvider(
                        adapter_provider=provider_name,
                        endpoint=self.endpoint,
                        api_key=self.api_key,
                        timeout_seconds=self.timeout_seconds,
                        fallback_to_estimation=False,
                    )
                elif provider_name in ("12306", "ctrip"):
                    provider = RailwayAPIProvider(
                        adapter_provider=provider_name,
                        endpoint=self.endpoint,
                        api_key=self.api_key,
                        timeout_seconds=self.timeout_seconds,
                        fallback_to_estimation=False,
                    )
                else:
                    continue

                result = provider.get_candidate_options(payload)
                has_data = any(opts for opts in result.values())
                if has_data:
                    self._active_provider = provider_name
                    logger.info("Successfully fetched data from %s", provider_name)

                    all_have_data = all(
                        result.get(t["name"]) for t in payload.get("travelers", [])
                    )
                    if not all_have_data:
                        logger.info(
                            "Partial data from %s, supplementing with estimation",
                            provider_name,
                        )
                        estimation = self._estimation.get_candidate_options(payload)
                        for name, opts in estimation.items():
                            if not result.get(name):
                                result[name] = opts
                    return result
            except Exception as e:
                logger.warning("Provider %s failed: %s", provider_name, e)
                continue

        logger.warning("All API providers failed, falling back to distance estimation")
        self._active_provider = "distance-estimation"
        return self._estimation.get_candidate_options(payload)

    def get_hub_data(
        self,
        origin: str,
        destination: str,
        date: str,
        hub_cities: List[str],
        dep0: datetime,
        dep1: datetime,
        shared_hub_to_dest_cache: Optional[Dict[str, List[dict]]] = None,
    ) -> Dict[str, Dict[str, List[dict]]]:
        for provider_name in self.provider_names:
            try:
                if provider_name in ("12306-direct", "12306-mcp", "12306", "ctrip"):
                    provider = RailwayAPIProvider(
                        adapter_provider=provider_name,
                        endpoint=self.endpoint,
                        api_key=self.api_key,
                        timeout_seconds=self.timeout_seconds,
                        fallback_to_estimation=False,
                    )
                else:
                    continue
                result = provider.get_hub_data(
                    origin, destination, date, hub_cities, dep0, dep1,
                    shared_hub_to_dest_cache=shared_hub_to_dest_cache,
                )
                if result:
                    return result
            except Exception as e:
                logger.debug("Hub query with %s failed: %s", provider_name, e)
                continue

        return self._estimation.get_hub_data(
            origin, destination, date, hub_cities, dep0, dep1,
            shared_hub_to_dest_cache=shared_hub_to_dest_cache,
        )


def _is_within_departure_window(option: dict, dep0: datetime, dep1: datetime) -> bool:
    legs = option.get("legs", [])
    if not legs:
        return False
    dep_time_str = legs[0].get("dep_time", "")
    if not dep_time_str:
        return False
    try:
        dep_time = datetime.strptime(dep_time_str, DT_FMT)
        return dep0 <= dep_time <= dep1
    except ValueError:
        return False


def fetch_and_generate(
    payload: dict,
    provider_name: str = "mock",
    cache: Optional[FileTTLCache] = None,
    api_endpoint: str = "",
    api_key: str = "",
) -> Dict[str, List[dict]]:
    """
    Unified entry point: fetch raw train data + generate strategies.

    Pipeline:
    1. Fetch direct train data per traveler (from API or estimation)
    2. Determine relevant transfer hubs (geographic, not hard-coded)
    3. Fetch hub transfer data (origin->hub, hub->destination)
    4. Apply strategy generation (same-train, transfer-merge, etc.)
    5. Return strategy-enriched candidate options with data source metadata

    Data source priority:
    - 12306/ctrip: Real-time API data (high reliability)
    - distance-estimation: Haversine-based estimation (low reliability, universal fallback)
    - external-api: Custom API endpoint (reliability depends on endpoint)
    """
    from strategy_generator import generate_all_strategies, get_relevant_hubs

    date = payload.get("date", datetime.now().strftime("%Y-%m-%d"))
    destination = payload.get("destination", "Beijing")
    all_travelers = payload.get("travelers", [])

    dep0_map: Dict[str, datetime] = {}
    dep1_map: Dict[str, datetime] = {}
    for t in all_travelers:
        name = t["name"]
        dep0_map[name] = datetime.strptime(
            t.get("earliest_departure", f"{date} 07:00"), DT_FMT
        )
        dep1_map[name] = datetime.strptime(
            t.get("latest_departure", f"{date} 15:00"), DT_FMT
        )

    cache_key = json.dumps(
        {
            "provider_name": provider_name,
            "destination": destination,
            "date": date,
            "travelers": all_travelers,
            "version": "v5_parallel_optimized",
        },
        ensure_ascii=False,
        sort_keys=True,
    )

    if cache:
        cached = cache.get(cache_key)
        if cached:
            logger.info("Cache hit for provider=%s", provider_name)
            return cached

    provider = _create_provider(provider_name, api_endpoint, api_key)

    direct_options = provider.get_candidate_options(payload)

    direct_has_same_train = False
    if len(all_travelers) >= 2:
        train_nos_per_traveler: Dict[str, Set[str]] = {}
        for t in all_travelers:
            name = t["name"]
            train_nos_per_traveler[name] = set()
            for opt in direct_options.get(name, []):
                for leg in opt.get("legs", []):
                    tn = leg.get("train_no", "")
                    if tn and not tn.startswith("G_UNKNOWN") and not tn.startswith("EST"):
                        train_nos_per_traveler[name].add(tn)
        names = list(train_nos_per_traveler.keys())
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                if train_nos_per_traveler[names[i]] & train_nos_per_traveler[names[j]]:
                    direct_has_same_train = True
                    break
            if direct_has_same_train:
                break

    hub_options: Dict[str, Dict[str, Dict[str, List[dict]]]] = {}
    shared_hub_to_dest_cache: Dict[str, List[dict]] = {}

    all_needed_hubs: Set[str] = set()
    for t in all_travelers:
        origin = t.get("origin", "")
        if origin and origin != destination:
            hubs = get_relevant_hubs(origin, destination, max_hubs=2)
            all_needed_hubs.update(hubs)

    if all_needed_hubs and hasattr(provider, "query_trains_for_city_pair"):
        for hub in all_needed_hubs:
            cache_key = f"{hub}->{destination}"
            if cache_key in shared_hub_to_dest_cache:
                continue
            try:
                h2d_trains = provider.query_trains_for_city_pair(hub, destination, date)
                if h2d_trains:
                    hub_dep0 = datetime.strptime(f"{date} 06:00", DT_FMT)
                    hub_dep1 = datetime.strptime(f"{date} 23:00", DT_FMT)
                    filtered = [
                        t for t in h2d_trains
                        if _is_within_departure_window(t, hub_dep0, hub_dep1)
                    ]
                    if filtered:
                        shared_hub_to_dest_cache[cache_key] = filtered
            except Exception as e:
                logger.debug("Pre-fetch hub->dest %s->%s failed: %s", hub, destination, e)

    def _fetch_hub_data_for_traveler(t: dict) -> Tuple[str, Dict[str, Dict[str, List[dict]]]]:
        name = t["name"]
        origin = t.get("origin", "")
        dep0 = dep0_map.get(name)
        dep1 = dep1_map.get(name)
        if not dep0 or not dep1 or origin == destination:
            return name, {}
        hub_cities = get_relevant_hubs(origin, destination, max_hubs=2)
        if not hub_cities:
            return name, {}
        if hasattr(provider, "get_hub_data"):
            traveler_hub_data = provider.get_hub_data(
                origin=origin,
                destination=destination,
                date=date,
                hub_cities=hub_cities,
                dep0=dep0,
                dep1=dep1,
                shared_hub_to_dest_cache=shared_hub_to_dest_cache,
            )
            return name, traveler_hub_data or {}
        return name, {}

    need_hub_query = True
    direct_option_count = sum(len(opts) for opts in direct_options.values())
    all_travelers_have_same_train = (
        direct_has_same_train
        and len(all_travelers) >= 2
        and all(
            any(o.get("strategy") == "same-train" for o in direct_options.get(t.get("name", ""), []))
            for t in all_travelers
        )
    )
    if all_travelers_have_same_train and direct_option_count >= 8:
        need_hub_query = False
        logger.info("All travelers have same-train options with %d total direct options, skipping hub queries", direct_option_count)
    elif direct_has_same_train and direct_option_count >= 8:
        need_hub_query = True
        logger.info("Same-train detected but not for all travelers (%d direct options), still querying hubs", direct_option_count)
    elif len(all_travelers) >= 2 and direct_option_count >= 20:
        has_diverse_strategies = False
        all_strategies = set()
        for name, opts in direct_options.items():
            for o in opts:
                s = o.get("strategy", "")
                if s:
                    all_strategies.add(s)
        if len(all_strategies) >= 3:
            has_diverse_strategies = True
        if has_diverse_strategies:
            need_hub_query = False
            logger.info("Sufficient direct options (%d) with diverse strategies %s, skipping hub queries", direct_option_count, all_strategies)

    if need_hub_query and len(all_travelers) > 0:
        max_workers = min(4, len(all_travelers))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_fetch_hub_data_for_traveler, t): t["name"]
                for t in all_travelers
            }
            for future in as_completed(futures, timeout=45):
                try:
                    name, data = future.result(timeout=25)
                    if data:
                        hub_options[name] = data
                except Exception as e:
                    traveler_name = futures[future]
                    logger.warning("Hub data fetch failed for %s: %s", traveler_name, e)

    strategy_options = generate_all_strategies(
        direct_options=direct_options,
        hub_options=hub_options,
        all_travelers=all_travelers,
        destination=destination,
        date=date,
        dep0_map=dep0_map,
        dep1_map=dep1_map,
    )

    for name in strategy_options:
        if not strategy_options[name]:
            if name in direct_options and direct_options[name]:
                strategy_options[name] = direct_options[name]
            else:
                logger.warning("No options generated for traveler %s", name)

    MAX_OPTIONS_PER_TRAVELER = 12
    STRATEGY_PRIORITY = {"same-train": 0, "partial-same-train": 1, "transfer-merge": 2, "synchronized-arrival": 3, "best-compromise": 4}
    for name in strategy_options:
        opts = strategy_options[name]
        if len(opts) > MAX_OPTIONS_PER_TRAVELER:
            opts_sorted = sorted(opts, key=lambda o: (
                STRATEGY_PRIORITY.get(o.get("strategy", ""), 5),
                o.get("total_minutes", 9999),
                o.get("total_price", 99999),
            ))
            strategy_options[name] = opts_sorted[:MAX_OPTIONS_PER_TRAVELER]
            logger.info("Trimmed options for %s from %d to %d", name, len(opts), MAX_OPTIONS_PER_TRAVELER)

    if cache:
        cache.set(cache_key, strategy_options)

    return strategy_options


def _create_provider(
    provider_name: str, api_endpoint: str = "", api_key: str = ""
) -> BaseCandidateProvider:
    """Create a data provider instance based on provider_name.

    Provider hierarchy (recommended for production):
      auto: 12306-direct -> 12306-mcp -> distance-estimation (DEFAULT)
      12306-direct: Direct 12306 public API, free, no key needed
      12306-mcp: External 12306-MCP server (npx -y 12306-mcp)
      12306: 12306 API proxy (requires endpoint + key)
      ctrip: Ctrip API proxy (requires endpoint + key)
      mock: Distance estimation only (for development)
      api: Custom external API endpoint
    """
    if provider_name == "mock":
        return EstimationProvider()
    elif provider_name == "12306-direct":
        return RailwayAPIProvider(
            adapter_provider="12306-direct",
            endpoint=api_endpoint,
            api_key=api_key,
        )
    elif provider_name == "12306-mcp":
        effective_endpoint = api_endpoint or os.getenv("TRIP_PLANNER_12306_MCP_URL", "")
        return RailwayAPIProvider(
            adapter_provider="12306-mcp",
            endpoint=effective_endpoint,
            api_key=api_key,
        )
    elif provider_name in ("12306", "ctrip"):
        effective_key = api_key or os.getenv(
            f"TRIP_PLANNER_{provider_name.upper()}_KEY", ""
        )
        effective_endpoint = api_endpoint or os.getenv(
            f"TRIP_PLANNER_{provider_name.upper()}_ENDPOINT", ""
        )
        return RailwayAPIProvider(
            adapter_provider=provider_name,
            endpoint=effective_endpoint,
            api_key=effective_key,
        )
    elif provider_name == "auto":
        return MultiProviderFacade(
            endpoint=api_endpoint,
            api_key=api_key,
        )
    elif provider_name == "api":
        effective_key = api_key or os.getenv("TRIP_PLANNER_API_KEY", "")
        if not api_endpoint:
            raise ProviderError("api_endpoint is required when provider_name=api")
        return APICandidateProvider(endpoint=api_endpoint, api_key=effective_key)
    else:
        raise ProviderError(
            f"Unsupported provider_name: {provider_name}. "
            "Supported: auto, 12306-direct, 12306-mcp, 12306, ctrip, mock, api"
        )


PROVIDER_DATA_SOURCE_MAP = {
    "mock": "distance-estimation",
    "api": "external-api",
    "12306": "12306",
    "12306-direct": "12306-direct",
    "12306-mcp": "12306-mcp",
    "ctrip": "ctrip",
    "auto": "multi-provider",
}


def resolve_provider(
    provider_name: str,
    cache: FileTTLCache,
    payload: dict,
    api_endpoint: str = "",
    api_key: str = "",
) -> Dict[str, List[dict]]:
    """
    Backward-compatible entry point for MCP server and other callers.
    Delegates to fetch_and_generate for the unified pipeline.
    """
    return fetch_and_generate(
        payload=payload,
        provider_name=provider_name,
        cache=cache,
        api_endpoint=api_endpoint,
        api_key=api_key,
    )


def get_data_source_for_provider(provider_name: str) -> str:
    return PROVIDER_DATA_SOURCE_MAP.get(provider_name, "distance-estimation")
