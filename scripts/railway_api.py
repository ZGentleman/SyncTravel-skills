import json
import logging
import os
import re
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from models import (
    DT_FMT,
    HSR_PRIMARY_STATION,
    STATION_ALIASES,
    CandidateOption,
    is_known_station,
    validate_station_for_city,
)

logger = logging.getLogger("trip_planner.railway_api")

_STATION_CODE_CACHE: Dict[str, str] = {}
_STATION_CODE_CACHE_TIME: float = 0.0
_STATION_CODE_CACHE_TTL: float = 86400.0
_LAST_12306_REQUEST_TIME: float = 0.0
_12306_MIN_INTERVAL: float = 0.5
_12306_RATE_LOCK = threading.Lock()
_SHARED_12306_SESSION = None
_SHARED_SESSION_LOCK = threading.Lock()

EN_TO_CN_STATION_MAP: Dict[str, str] = {
    "Beijing West": "北京西", "Beijing South": "北京南", "Beijing": "北京",
    "Beijing North": "北京北", "Beijing East": "北京东", "Beijing Chaoyang": "北京朝阳",
    "Shanghai Hongqiao": "上海虹桥", "Shanghai": "上海", "Shanghai South": "上海南",
    "Shanghai West": "上海西",
    "Guangzhou South": "广州南", "Guangzhou North": "广州北", "Guangzhou": "广州",
    "Guangzhou East": "广州东",
    "Shenzhen North": "深圳北", "Shenzhen": "深圳", "Shenzhen East": "深圳东",
    "Futian": "福田", "Shenzhen Pingshan": "深圳坪山",
    "Wuhan": "武汉", "Wuhan East": "武汉东", "Hankou": "汉口", "Wuchang": "武昌",
    "Zhengzhou East": "郑州东", "Zhengzhou": "郑州", "Zhengzhou West": "郑州西",
    "Changsha South": "长沙南", "Changsha": "长沙",
    "Nanjing South": "南京南", "Nanjing": "南京", "Nanjing East": "南京东",
    "Chengdu East": "成都东", "Chengdu": "成都",
    "Hangzhou East": "杭州东", "Hangzhou": "杭州", "Hangzhou South": "杭州南",
    "Shijiazhuang": "石家庄",
    "Xi'an North": "西安北", "Xi'an": "西安",
    "Jinan West": "济南西", "Jinan": "济南",
    "Hefei South": "合肥南", "Hefei": "合肥",
    "Nanchang West": "南昌西", "Nanchang": "南昌",
    "Fuzhou South": "福州南", "Fuzhou": "福州",
    "Kunming": "昆明", "Kunming South": "昆明南",
    "Guiyang North": "贵阳北", "Guiyang": "贵阳",
    "Chongqing North": "重庆北", "Chongqing West": "重庆西", "Chongqing": "重庆",
    "Tianjin West": "天津西", "Tianjin South": "天津南", "Tianjin": "天津",
    "Shenyang North": "沈阳北", "Shenyang": "沈阳",
    "Harbin West": "哈尔滨西", "Harbin": "哈尔滨",
    "Changchun West": "长春西", "Changchun": "长春",
    "Taiyuan South": "太原南", "Taiyuan": "太原",
    "Lanzhou West": "兰州西", "Lanzhou": "兰州",
    "Xuzhou East": "徐州东", "Xuzhou": "徐州",
    "Qingdao": "青岛", "Dalian": "大连",
    "Suzhou North": "苏州北", "Suzhou": "苏州",
    "Wuxi East": "无锡东", "Wuxi": "无锡",
    "Ningbo": "宁波",
    "Xiamen North": "厦门北", "Xiamen": "厦门",
    "Nanning East": "南宁东", "Nanning": "南宁",
    "Zhuhai": "珠海",
    "Mianyang": "绵阳",
    "Leshan": "乐山",
    "Yibin": "宜宾",
    "Deyang": "德阳",
    "Luoyang Longmen": "洛阳龙门", "Luoyang": "洛阳",
    "Xinyang East": "信阳东", "Xinyang": "信阳",
    "Zhumadian": "驻马店",
    "Xuchang East": "许昌东", "Xuchang": "许昌",
    "Luohe": "漯河",
    "Zhenjiang South": "镇江南", "Zhenjiang": "镇江",
    "Changzhou North": "常州北", "Changzhou": "常州",
    "Jiaxing South": "嘉兴南", "Jiaxing": "嘉兴",
    "Shaoxing North": "绍兴北", "Shaoxing": "绍兴",
    "Jinhua": "金华",
    "Taizhou": "台州",
    "Wenzhou South": "温州南", "Wenzhou": "温州",
    "Quzhou": "衢州",
    "Shangrao": "上饶",
    "Yingtan": "鹰潭",
    "Jiujiang": "九江",
    "Ganzhou": "赣州",
    "Yichang East": "宜昌东", "Yichang": "宜昌",
    "Xiangyang East": "襄阳东", "Xiangyang": "襄阳",
    "Jingmen": "荆门",
    "Shiyan": "十堰",
    "Enshi": "恩施",
    "Yueyang East": "岳阳东", "Yueyang": "岳阳",
    "Hengyang East": "衡阳东", "Hengyang": "衡阳",
    "Chenzhou West": "郴州西", "Chenzhou": "郴州",
    "Shaoguan": "韶关",
    "Qingyuan": "清远",
    "Huizhou": "惠州",
    "Dongguan": "东莞",
    "Foshan": "佛山",
    "Zhongshan": "中山",
    "Jiangmen": "江门",
    "Zhaoqing East": "肇庆东", "Zhaoqing": "肇庆",
    "Baoding East": "保定东", "Baoding": "保定",
    "Handan East": "邯郸东", "Handan": "邯郸",
    "Xingtai East": "邢台东", "Xingtai": "邢台",
    "Cangzhou West": "沧州西", "Cangzhou": "沧州",
    "Langfang": "廊坊",
    "Tangshan": "唐山",
    "Qinhuangdao": "秦皇岛",
    "Zibo": "淄博",
    "Weifang": "潍坊",
    "Linyi": "临沂",
    "Tai'an": "泰安",
    "Jining": "济宁",
    "Dezhou East": "德州东", "Dezhou": "德州",
    "Liaocheng": "聊城",
    "Bengbu South": "蚌埠南", "Bengbu": "蚌埠",
    "Fuyang": "阜阳",
    "Huaibei": "淮北",
    "Huainan": "淮南",
    "Anqing": "安庆",
    "Huangshan North": "黄山北", "Huangshan": "黄山",
    "Chuzhou": "滁州",
    "Putian": "莆田",
    "Quanzhou": "泉州",
    "Zhangzhou": "漳州",
    "Longyan": "龙岩",
    "Sanming": "三明",
    "Nanping": "南平",
    "Nantong": "南通",
    "Yangzhou East": "扬州东", "Yangzhou": "扬州",
    "Yancheng": "盐城",
    "Huaian": "淮安",
    "Lianyungang": "连云港",
    "Xuzhou": "徐州",
    "Taizhou(Jiangsu)": "泰州",
    "Suqian": "宿迁",
    "Mudanjiang": "牡丹江",
    "Jiamusi": "佳木斯",
    "Qiqihar": "齐齐哈尔",
    "Daqing": "大庆",
    "Baotou": "包头",
    "Hohhot East": "呼和浩特东", "Hohhot": "呼和浩特",
    "Ordos": "鄂尔多斯",
    "Urumqi": "乌鲁木齐",
    "Yinchuan": "银川",
    "Xining": "西宁",
    "Lhasa": "拉萨",
    "Guilin": "桂林",
    "Liuzhou": "柳州",
    "Beihai": "北海",
    "Wuzhou": "梧州",
    "Yulin(Guangxi)": "玉林",
    "Zunyi": "遵义",
    "Liupanshui": "六盘水",
    "Dali": "大理",
    "Lijiang": "丽江",
    "Qujing": "曲靖",
    "Yuxi": "玉溪",
    "Panzhihua": "攀枝花",
    "Dazhou": "达州",
    "Guangyuan": "广元",
    "Nanchong": "南充",
    "Suining": "遂宁",
    "Neijiang": "内江",
    "Zigong": "自贡",
    "Luzhou": "泸州",
    "Meishan": "眉山",
    "Ya'an": "雅安",
    "Zhangjiajie": "张家界",
    "Huaihua South": "怀化南", "Huaihua": "怀化",
    "Changde": "常德",
    "Yiyang": "益阳",
    "Loudi": "娄底",
    "Shaoyang": "邵阳",
    "Yongzhou": "永州",
    "Chenzhou West": "郴州西",
    "Jishou": "吉首",
    "Pingxiang": "萍乡",
    "Xinyu": "新余",
    "Jingdezhen": "景德镇",
    "Fuzhou(Jiangxi)": "抚州",
    "Ji'an": "吉安",
    "Pingdingshan": "平顶山",
    "Anyang East": "安阳东", "Anyang": "安阳",
    "Xinxiang East": "新乡东", "Xinxiang": "新乡",
    "Kaifeng": "开封",
    "Shangqiu": "商丘",
    "Zhoukou": "周口",
    "Luohe": "漯河",
    "Nanyang": "南阳",
    "Xinxiang": "新乡",
    "Jiaozuo": "焦作",
    "Hebi": "鹤壁",
    "Puyang": "濮阳",
    "Xuchang": "许昌",
    "Luohe": "漯河",
    "Sanmenxia": "三门峡",
    "Zhumadian": "驻马店",
    "Linfen": "临汾",
    "Datong South": "大同南", "Datong": "大同",
    "Yuncheng North": "运城北", "Yuncheng": "运城",
    "Changzhi North": "长治北", "Changzhi": "长治",
    "Jinzhong": "晋中",
    "Xinzhou West": "忻州西", "Xinzhou": "忻州",
    "Yangquan North": "阳泉北", "Yangquan": "阳泉",
    "Luliang": "吕梁",
    "Jincheng": "晋城",
    "Shuozhou": "朔州",
}


class RailwayAPIError(RuntimeError):
    def __init__(self, message: str, provider: str = "", status_code: int = 0):
        self.provider = provider
        self.status_code = status_code
        super().__init__(message)


class RailwayAPIAdapter(ABC):
    @abstractmethod
    def query_trains(
        self,
        origin_station: str,
        destination_station: str,
        date: str,
    ) -> List[dict]:
        raise NotImplementedError

    @abstractmethod
    def get_provider_name(self) -> str:
        raise NotImplementedError

    def query_trains_for_city_pair(
        self,
        origin_city: str,
        destination_city: str,
        date: str,
    ) -> List[dict]:
        origin_station = HSR_PRIMARY_STATION.get(origin_city, origin_city)
        dest_station = HSR_PRIMARY_STATION.get(destination_city, destination_city)
        return self.query_trains(origin_station, dest_station, date)

    def query_transfer_routes(
        self,
        origin_station: str,
        destination_station: str,
        date: str,
        hub_stations: List[str] = None,
    ) -> List[dict]:
        """
        Query one-transfer routes by combining two direct queries.

        For each hub station, queries:
        1. origin -> hub (first leg)
        2. hub -> destination (second leg)

        Returns combined options with transfer information.
        Subclasses can override this for API-native transfer queries.
        """
        if not hub_stations:
            return []

        all_options = []
        for hub in hub_stations:
            if hub == origin_station or hub == destination_station:
                continue

            try:
                first_legs = self.query_trains(origin_station, hub, date)
                second_legs = self.query_trains(hub, destination_station, date)

                for first in first_legs:
                    for second in second_legs:
                        first_legs_data = first.get("legs", [])
                        second_legs_data = second.get("legs", [])
                        if not first_legs_data or not second_legs_data:
                            continue

                        first_arr_str = first_legs_data[-1].get("arr_time", "")
                        second_dep_str = second_legs_data[0].get("dep_time", "")
                        if not first_arr_str or not second_dep_str:
                            continue

                        try:
                            first_arr = datetime.strptime(first_arr_str, DT_FMT)
                            second_dep = datetime.strptime(second_dep_str, DT_FMT)
                        except ValueError:
                            continue

                        transfer_gap = (second_dep - first_arr).total_seconds() / 60
                        if transfer_gap < 0:
                            transfer_gap += 1440

                        if transfer_gap < 15 or transfer_gap > 180:
                            continue

                        first_price = first.get("total_price", 0)
                        second_price = second.get("total_price", 0)
                        total_price = round(float(first_price) + float(second_price), 2)

                        first_minutes = first.get("total_minutes", 0)
                        second_minutes = second.get("total_minutes", 0)
                        total_minutes = int(first_minutes) + int(transfer_gap) + int(second_minutes)

                        combined_legs = []
                        for leg in first_legs_data:
                            combined_legs.append(dict(leg))
                        for leg in second_legs_data:
                            combined_legs.append(dict(leg))

                        final_arr_str = second_legs_data[-1].get("arr_time", "")

                        option = {
                            "strategy": "transfer-merge",
                            "arrival_time": final_arr_str,
                            "total_minutes": total_minutes,
                            "total_price": total_price,
                            "total_transfers": 1,
                            "train_type": "HSR",
                            "seat_type": first.get("seat_type", "second-class"),
                            "shared_train_ratio": 0.0,
                            "merge_hub": hub,
                            "data_source": self.get_provider_name(),
                            "legs": combined_legs,
                        }
                        try:
                            CandidateOption.model_validate(option)
                            all_options.append(option)
                        except Exception:
                            continue

            except RailwayAPIError as e:
                logger.debug("Transfer query via %s failed: %s", hub, e)
                continue

        return all_options


class China12306Adapter(RailwayAPIAdapter):
    """
    12306 API adapter for real-time train data.

    Supports both direct and transfer route queries.

    Configuration:
    - TRIP_PLANNER_12306_ENDPOINT: API proxy endpoint URL
    - TRIP_PLANNER_12306_KEY: API authentication key

    The adapter normalizes 12306 API responses into our standard format.
    It handles multiple response formats from different 12306 API proxies.
    """

    def __init__(
        self,
        endpoint: str = "",
        api_key: str = "",
        timeout_seconds: float = 10.0,
    ):
        self.endpoint = endpoint or os.getenv("TRIP_PLANNER_12306_ENDPOINT", "")
        self.api_key = api_key or os.getenv("TRIP_PLANNER_12306_KEY", "")
        self.timeout_seconds = timeout_seconds

        if not self.endpoint:
            logger.warning(
                "12306 adapter initialized without endpoint. "
                "Set TRIP_PLANNER_12306_ENDPOINT env var or pass endpoint parameter."
            )

    def get_provider_name(self) -> str:
        return "12306"

    def query_trains(
        self,
        origin_station: str,
        destination_station: str,
        date: str,
    ) -> List[dict]:
        if not self.endpoint:
            raise RailwayAPIError(
                "12306 API endpoint not configured. Set TRIP_PLANNER_12306_ENDPOINT.",
                provider="12306",
            )

        try:
            import requests
        except ImportError:
            raise RailwayAPIError("requests library required for 12306 adapter", provider="12306")

        params = {
            "from_station": origin_station,
            "to_station": destination_station,
            "date": date,
        }
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            response = requests.get(
                self.endpoint,
                params=params,
                headers=headers,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
        except requests.Timeout:
            raise RailwayAPIError("12306 API request timed out", provider="12306", status_code=408)
        except requests.ConnectionError:
            raise RailwayAPIError("Cannot connect to 12306 API endpoint", provider="12306")
        except requests.HTTPError as e:
            raise RailwayAPIError(
                f"12306 API returned HTTP {e.response.status_code}",
                provider="12306",
                status_code=e.response.status_code,
            )
        except (json.JSONDecodeError, ValueError):
            raise RailwayAPIError("Invalid JSON response from 12306 API", provider="12306")

        return self._normalize_12306_response(data, origin_station, destination_station, date)

    def _normalize_12306_response(
        self,
        data: dict,
        origin_station: str,
        destination_station: str,
        date: str,
    ) -> List[dict]:
        trains = data.get("data", data.get("result", []))
        if isinstance(trains, dict):
            trains = trains.get("trains", trains.get("list", []))

        normalized = []
        for train in trains:
            try:
                train_no = train.get("station_train_code", train.get("train_no", ""))
                if not train_no:
                    continue

                dep_time_raw = train.get("start_time", train.get("dep_time", ""))
                arr_time_raw = train.get("arrive_time", train.get("arr_time", ""))
                duration_raw = train.get("run_time", train.get("duration", ""))

                dep_time = f"{date} {dep_time_raw}" if dep_time_raw else ""
                arr_time = f"{date} {arr_time_raw}" if arr_time_raw else ""

                if duration_raw and ":" in str(duration_raw):
                    parts = str(duration_raw).split(":")
                    duration_minutes = int(parts[0]) * 60 + int(parts[1])
                else:
                    if dep_time and arr_time:
                        try:
                            dt_dep = datetime.strptime(dep_time, DT_FMT)
                            dt_arr = datetime.strptime(arr_time, DT_FMT)
                            if dt_arr < dt_dep:
                                dt_arr += timedelta(days=1)
                            duration_minutes = int((dt_arr - dt_dep).total_seconds() // 60)
                        except ValueError:
                            continue
                    else:
                        continue

                prices = train.get("prices", train.get("price", {}))
                if isinstance(prices, dict):
                    price = float(prices.get("second_class", prices.get("A3", 0)))
                    if price == 0:
                        price = float(prices.get("first_class", prices.get("A2", 0)))
                else:
                    price = float(prices) if prices else 0

                seat_type = "second-class"
                if isinstance(prices, dict):
                    if prices.get("second_class") or prices.get("A3"):
                        seat_type = "second-class"
                    elif prices.get("first_class") or prices.get("A2"):
                        seat_type = "first-class"

                dep_st = train.get("from_station_name", train.get("dep_station", origin_station))
                arr_st = train.get("to_station_name", train.get("arr_station", destination_station))

                train_type = "HSR" if train_no.startswith("G") else "D-series" if train_no.startswith("D") else "other"

                option = {
                    "strategy": "synchronized-arrival",
                    "arrival_time": arr_time,
                    "total_minutes": duration_minutes,
                    "total_price": price,
                    "total_transfers": 0,
                    "train_type": train_type,
                    "seat_type": seat_type,
                    "shared_train_ratio": 0.0,
                    "data_source": "12306",
                    "legs": [
                        {
                            "train_no": train_no,
                            "dep_station": dep_st,
                            "arr_station": arr_st,
                            "dep_time": dep_time,
                            "arr_time": arr_time,
                        }
                    ],
                }
                try:
                    CandidateOption.model_validate(option)
                    normalized.append(option)
                except Exception as e:
                    logger.debug("Skipping invalid train %s: %s", train_no, e)
            except Exception as e:
                logger.debug("Error normalizing train data: %s", e)
                continue

        return normalized


class CtripAdapter(RailwayAPIAdapter):
    """
    Ctrip API adapter for real-time train data.

    Configuration:
    - TRIP_PLANNER_CTRIP_ENDPOINT: API endpoint URL
    - TRIP_PLANNER_CTRIP_KEY: API authentication key
    """

    def __init__(
        self,
        endpoint: str = "",
        api_key: str = "",
        timeout_seconds: float = 10.0,
    ):
        self.endpoint = endpoint or os.getenv("TRIP_PLANNER_CTRIP_ENDPOINT", "")
        self.api_key = api_key or os.getenv("TRIP_PLANNER_CTRIP_KEY", "")
        self.timeout_seconds = timeout_seconds

    def get_provider_name(self) -> str:
        return "ctrip"

    def query_trains(
        self,
        origin_station: str,
        destination_station: str,
        date: str,
    ) -> List[dict]:
        if not self.endpoint:
            raise RailwayAPIError(
                "Ctrip API endpoint not configured. Set TRIP_PLANNER_CTRIP_ENDPOINT.",
                provider="ctrip",
            )

        try:
            import requests
        except ImportError:
            raise RailwayAPIError("requests library required for Ctrip adapter", provider="ctrip")

        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "fromStation": origin_station,
            "toStation": destination_station,
            "date": date,
            "trainType": "G",
        }

        try:
            response = requests.post(
                self.endpoint,
                json=payload,
                headers=headers,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
        except requests.Timeout:
            raise RailwayAPIError("Ctrip API request timed out", provider="ctrip", status_code=408)
        except requests.ConnectionError:
            raise RailwayAPIError("Cannot connect to Ctrip API endpoint", provider="ctrip")
        except requests.HTTPError as e:
            raise RailwayAPIError(
                f"Ctrip API returned HTTP {e.response.status_code}",
                provider="ctrip",
                status_code=e.response.status_code,
            )

        return self._normalize_ctrip_response(data, origin_station, destination_station, date)

    def _normalize_ctrip_response(
        self,
        data: dict,
        origin_station: str,
        destination_station: str,
        date: str,
    ) -> List[dict]:
        trains = data.get("data", data.get("trainList", []))
        if isinstance(trains, dict):
            trains = trains.get("trains", [])

        normalized = []
        for train in trains:
            try:
                train_no = train.get("trainNo", train.get("train_no", train.get("stationTrainCode", "")))
                if not train_no:
                    continue

                dep_time_raw = train.get("departTime", train.get("dep_time", ""))
                arr_time_raw = train.get("arriveTime", train.get("arr_time", ""))

                dep_time = f"{date} {dep_time_raw[:5]}" if dep_time_raw else ""
                arr_time = f"{date} {arr_time_raw[:5]}" if arr_time_raw else ""

                duration_minutes = train.get("duration", train.get("runTimeMinutes", 0))
                if isinstance(duration_minutes, str) and ":" in duration_minutes:
                    parts = duration_minutes.split(":")
                    duration_minutes = int(parts[0]) * 60 + int(parts[1])
                duration_minutes = int(duration_minutes) if duration_minutes else 0

                if duration_minutes == 0 and dep_time and arr_time:
                    try:
                        dt_dep = datetime.strptime(dep_time, DT_FMT)
                        dt_arr = datetime.strptime(arr_time, DT_FMT)
                        if dt_arr < dt_dep:
                            dt_arr += timedelta(days=1)
                        duration_minutes = int((dt_arr - dt_dep).total_seconds() // 60)
                    except ValueError:
                        continue

                prices = train.get("prices", {})
                if isinstance(prices, list) and prices:
                    price = float(prices[0].get("price", 0))
                    seat_type = prices[0].get("seatType", "second-class")
                elif isinstance(prices, dict):
                    price = float(prices.get("secondClass", 0))
                    seat_type = "second-class"
                else:
                    price = 0
                    seat_type = "second-class"

                dep_st = train.get("fromStation", train.get("dep_station", origin_station))
                arr_st = train.get("toStation", train.get("arr_station", destination_station))

                train_type = "HSR" if train_no.startswith("G") else "D-series" if train_no.startswith("D") else "other"

                option = {
                    "strategy": "synchronized-arrival",
                    "arrival_time": arr_time,
                    "total_minutes": duration_minutes,
                    "total_price": price,
                    "total_transfers": 0,
                    "train_type": train_type,
                    "seat_type": seat_type,
                    "shared_train_ratio": 0.0,
                    "data_source": "ctrip",
                    "legs": [
                        {
                            "train_no": train_no,
                            "dep_station": dep_st,
                            "arr_station": arr_st,
                            "dep_time": dep_time,
                            "arr_time": arr_time,
                        }
                    ],
                }
                try:
                    CandidateOption.model_validate(option)
                    normalized.append(option)
                except Exception as e:
                    logger.debug("Skipping invalid train %s from Ctrip: %s", train_no, e)
            except Exception as e:
                logger.debug("Error normalizing Ctrip data: %s", e)
                continue

        return normalized


class AggregatedAdapter(RailwayAPIAdapter):
    """
    Aggregated adapter that queries multiple providers and merges results.

    Industrial pattern: query multiple data sources, deduplicate,
    and return the most complete dataset.

    Fallback chain: tries providers in order until one succeeds.
    If multiple succeed, merges results with deduplication.
    """

    def __init__(
        self,
        providers: List[str] = None,
        endpoint: str = "",
        api_key: str = "",
        timeout_seconds: float = 10.0,
    ):
        self.provider_names = providers or ["12306", "ctrip"]
        self.endpoint = endpoint
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def get_provider_name(self) -> str:
        return "aggregated"

    def query_trains(
        self,
        origin_station: str,
        destination_station: str,
        date: str,
    ) -> List[dict]:
        all_trains: List[dict] = []
        seen_train_nos: set = set()
        successful_providers = []

        for provider_name in self.provider_names:
            try:
                adapter = get_adapter(
                    provider_name,
                    endpoint=self.endpoint,
                    api_key=self.api_key,
                    timeout_seconds=self.timeout_seconds,
                )
                trains = adapter.query_trains(origin_station, destination_station, date)
                successful_providers.append(provider_name)

                for train in trains:
                    train_no = ""
                    for leg in train.get("legs", []):
                        train_no = leg.get("train_no", "")
                        break

                    if train_no and train_no not in seen_train_nos:
                        seen_train_nos.add(train_no)
                        train["data_source"] = provider_name
                        all_trains.append(train)
                    elif train_no and train_no in seen_train_nos:
                        for existing in all_trains:
                            for leg in existing.get("legs", []):
                                if leg.get("train_no") == train_no:
                                    if existing.get("data_source", "") != provider_name:
                                        existing["data_source"] = f"{existing.get('data_source', '')}+{provider_name}"
                                    break

            except RailwayAPIError as e:
                logger.debug("Provider %s failed: %s", provider_name, e)
                continue
            except Exception as e:
                logger.debug("Unexpected error from %s: %s", provider_name, e)
                continue

        if not all_trains and successful_providers:
            logger.warning(
                "Providers %s returned data but no valid trains for %s->%s",
                successful_providers, origin_station, destination_station,
            )

        return all_trains


def _load_station_codes_from_file(cache_dir: str = ".cache/12306") -> Dict[str, str]:
    cache_path = Path(cache_dir) / "station_codes.json"
    if cache_path.exists():
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                return data
        except Exception:
            pass
    return {}


def _save_station_codes_to_file(codes: Dict[str, str], cache_dir: str = ".cache/12306") -> None:
    try:
        cache_path = Path(cache_dir)
        cache_path.mkdir(parents=True, exist_ok=True)
        (cache_path / "station_codes.json").write_text(
            json.dumps(codes, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass


def fetch_station_codes(force_refresh: bool = False) -> Dict[str, str]:
    global _STATION_CODE_CACHE, _STATION_CODE_CACHE_TIME

    now = time.time()
    if (
        not force_refresh
        and _STATION_CODE_CACHE
        and (now - _STATION_CODE_CACHE_TIME) < _STATION_CODE_CACHE_TTL
    ):
        return _STATION_CODE_CACHE

    if not force_refresh:
        file_codes = _load_station_codes_from_file()
        if file_codes:
            _STATION_CODE_CACHE = file_codes
            _STATION_CODE_CACHE_TIME = now
            return _STATION_CODE_CACHE

    try:
        import requests as req

        url = "https://kyfw.12306.cn/otn/resources/js/framework/station_name.js"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        resp = req.get(url, headers=headers, timeout=10, verify=False)
        resp.raise_for_status()

        matches = re.findall(r"@([a-z]+)\|([A-Z]+)\|([^\|]+)\|([^\|]+)\|([^\|]+)", resp.text)
        codes: Dict[str, str] = {}
        for match in matches:
            pinyin = match[0]
            telecode = match[1]
            cn_name = match[2]
            codes[cn_name] = telecode
            codes[pinyin] = telecode

        if not codes:
            matches2 = re.findall(r"([\u4e00-\u9fa5]+)\|([A-Z]+)", resp.text)
            for cn_name, telecode in matches2:
                codes[cn_name] = telecode

        if codes:
            _STATION_CODE_CACHE = codes
            _STATION_CODE_CACHE_TIME = now
            _save_station_codes_to_file(codes)
            logger.info("Loaded %d station codes from 12306", len(codes))
            return codes

    except ImportError:
        logger.warning("requests library required for fetching station codes")
    except Exception as e:
        logger.warning("Failed to fetch station codes from 12306: %s", e)

    if not _STATION_CODE_CACHE:
        _STATION_CODE_CACHE = _build_fallback_station_codes()
        _STATION_CODE_CACHE_TIME = now
    return _STATION_CODE_CACHE


def _build_fallback_station_codes() -> Dict[str, str]:
    codes: Dict[str, str] = {}
    known_codes = {
        "北京": "BJP", "北京西": "BXP", "北京南": "VNP", "北京北": "VAP",
        "上海": "SHH", "上海虹桥": "AOH", "上海南": "SNH",
        "广州": "GZQ", "广州南": "IZQ", "广州东": "GGQ",
        "深圳": "SZQ", "深圳北": "IOQ",
        "武汉": "WHN", "武汉站": "WHN", "汉口": "HKN", "武昌": "WCN",
        "郑州": "ZZF", "郑州东": "ZAF", "郑州西": "ZXF",
        "长沙": "CSQ", "长沙南": "CWQ",
        "南京": "NJH", "南京南": "NKH", "南京东": "NDH",
        "成都": "CDW", "成都东": "ICW",
        "杭州": "HZH", "杭州东": "HGH", "杭州南": "XAH",
        "石家庄": "SJP",
        "西安": "XAY", "西安北": "EAY",
        "济南": "JNK", "济南西": "JGK",
        "合肥": "HFH", "合肥南": "ENH",
        "南昌": "NXG", "南昌西": "NXG",
        "福州": "FZS", "福州南": "FYS",
        "昆明": "KMM",
        "贵阳": "GIW",
        "重庆": "CQW", "重庆北": "CUW", "重庆西": "CXW",
        "天津": "TJP", "天津西": "TXP", "天津南": "TIP",
        "沈阳": "SYT", "沈阳北": "SBT",
        "哈尔滨": "HBB", "哈尔滨西": "VAB",
        "长春": "CCT", "长春西": "CRT",
        "太原": "TYV", "太原南": "TNV",
        "兰州": "LZJ", "兰州西": "LXJ",
        "徐州": "XCH", "徐州东": "XUH",
        "青岛": "QDK",
        "大连": "DLT",
        "苏州": "SZH", "苏州北": "OHH",
        "无锡": "WXH", "无锡东": "WTH",
        "宁波": "NGH",
        "厦门": "XMS", "厦门北": "XKS",
        "南宁": "NNZ", "南宁东": "NDZ",
        "珠海": "ZHQ",
    }
    cn_to_en = {}
    for city, stations in STATION_ALIASES.items():
        for st in stations:
            if st in known_codes:
                cn_to_en[st] = known_codes[st]
                codes[st] = known_codes[st]
    for cn_name, code in known_codes.items():
        codes[cn_name] = code
    return codes


def _get_station_telecode(station_name: str) -> Optional[str]:
    codes = fetch_station_codes()
    if station_name in codes:
        return codes[station_name]
    from station_repository import get_primary_station
    primary = get_primary_station(station_name)
    if primary in codes:
        return codes[primary]
    for cn_name, code in codes.items():
        if station_name in cn_name or cn_name in station_name:
            return code
    return None


def _rate_limit_12306() -> None:
    global _LAST_12306_REQUEST_TIME
    with _12306_RATE_LOCK:
        now = time.time()
        elapsed = now - _LAST_12306_REQUEST_TIME
        if elapsed < _12306_MIN_INTERVAL:
            time.sleep(_12306_MIN_INTERVAL - elapsed)
        _LAST_12306_REQUEST_TIME = time.time()


class China12306DirectAdapter(RailwayAPIAdapter):
    """
    Direct 12306 public API adapter - NO API key required.

    Queries the official 12306 leftTicket/query endpoint directly.
    Uses station_name.js for station code mapping.

    This is the PRIMARY data source for production use.
    Free, real-time, no registration needed.

    Rate limit: 1 request/second to avoid IP blocking.
    """

    STATION_NAME_JS_URL = "https://kyfw.12306.cn/otn/resources/js/framework/station_name.js"
    LEFT_TICKET_URL = "https://kyfw.12306.cn/otn/leftTicket/query"
    LEFT_TICKET_URLS = [
        "https://kyfw.12306.cn/otn/leftTicket/query",
        "https://kyfw.12306.cn/otn/leftTicket/queryZ",
        "https://kyfw.12306.cn/otn/leftTicket/queryA",
        "https://kyfw.12306.cn/otn/leftTicket/queryG",
    ]

    def __init__(self, timeout_seconds: float = 10.0, **kwargs):
        self.timeout_seconds = timeout_seconds
        self._station_codes: Optional[Dict[str, str]] = None

    def get_provider_name(self) -> str:
        return "12306-direct"

    def _ensure_station_codes(self) -> Dict[str, str]:
        if self._station_codes is None:
            self._station_codes = fetch_station_codes()
        return self._station_codes

    def _resolve_station_code(self, station_name: str) -> str:
        codes = self._ensure_station_codes()
        if station_name in codes:
            return codes[station_name]
        cn_name = EN_TO_CN_STATION_MAP.get(station_name)
        if cn_name and cn_name in codes:
            return codes[cn_name]
        code = _get_station_telecode(station_name)
        if code:
            return code
        for cn_name_key, telecode in codes.items():
            if station_name in cn_name_key or cn_name_key in station_name:
                return telecode
        logger.warning("Cannot resolve station code for '%s', using name as-is", station_name)
        return station_name

    def query_trains(
        self,
        origin_station: str,
        destination_station: str,
        date: str,
    ) -> List[dict]:
        try:
            import requests as req
        except ImportError:
            raise RailwayAPIError(
                "requests library required for 12306-direct adapter",
                provider="12306-direct",
            )

        from_code = self._resolve_station_code(origin_station)
        to_code = self._resolve_station_code(destination_station)

        if not from_code or not to_code:
            raise RailwayAPIError(
                f"Cannot resolve station codes: {origin_station}({from_code}), {destination_station}({to_code})",
                provider="12306-direct",
            )

        global _SHARED_12306_SESSION
        with _SHARED_SESSION_LOCK:
            if _SHARED_12306_SESSION is None:
                _SHARED_12306_SESSION = req.Session()
                _SHARED_12306_SESSION.headers.update({
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                })
                try:
                    _rate_limit_12306()
                    init_resp = _SHARED_12306_SESSION.get(
                        "https://kyfw.12306.cn/otn/leftTicket/init",
                        timeout=self.timeout_seconds,
                        verify=False,
                    )
                    logger.debug("12306 init page status: %s", init_resp.status_code)
                except Exception as e:
                    logger.debug("12306 init page failed: %s (continuing anyway)", e)

        _SHARED_12306_SESSION.headers.update({
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": "https://kyfw.12306.cn/otn/leftTicket/init",
            "X-Requested-With": "XMLHttpRequest",
        })

        params = {
            "leftTicketDTO.train_date": date,
            "leftTicketDTO.from_station": from_code,
            "leftTicketDTO.to_station": to_code,
            "purpose_codes": "ADULT",
        }

        _rate_limit_12306()

        last_error = None
        for url in self.LEFT_TICKET_URLS:
            try:
                resp = _SHARED_12306_SESSION.get(
                    url,
                    params=params,
                    timeout=self.timeout_seconds,
                    verify=False,
                )
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                    except ValueError:
                        logger.debug("12306-direct non-JSON response from %s", url)
                        continue
                    if data.get("httpstatus") == 200:
                        return self._parse_12306_response(
                            data, origin_station, destination_station, date
                        )
                    elif "c_url" in data or "c_name" in data:
                        logger.debug("12306-direct redirect hint from %s, trying next", url)
                        continue
                    else:
                        logger.debug("12306-direct unexpected response from %s: %s", url, str(data)[:200])
                        continue
                elif resp.status_code == 302:
                    logger.debug("12306-direct 302 redirect from %s", url)
                    continue
                else:
                    logger.debug("12306-direct status %d from %s", resp.status_code, url)
            except req.Timeout:
                last_error = RailwayAPIError(
                    "12306 direct API timed out", provider="12306-direct", status_code=408
                )
            except req.ConnectionError:
                last_error = RailwayAPIError(
                    "Cannot connect to 12306", provider="12306-direct"
                )
            except Exception as e:
                last_error = RailwayAPIError(
                    f"12306 direct query error: {e}", provider="12306-direct"
                )
            _rate_limit_12306()

        if last_error:
            raise last_error
        raise RailwayAPIError(
            "All 12306 query endpoints failed", provider="12306-direct"
        )

    def _parse_12306_response(
        self,
        data: dict,
        origin_station: str,
        destination_station: str,
        date: str,
    ) -> List[dict]:
        raw_results = data.get("data", {}).get("result", [])
        if not raw_results:
            return []

        station_map = data.get("data", {}).get("map", {})
        if not station_map:
            station_map = self._build_station_map_from_codes()

        normalized = []
        for raw in raw_results:
            try:
                fields = raw.split("|")
                if len(fields) < 35:
                    continue

                train_no = fields[3]
                if not train_no:
                    continue

                if not train_no.startswith("G") and not train_no.startswith("D") and not train_no.startswith("C"):
                    continue

                dep_time_raw = fields[8]
                arr_time_raw = fields[9]
                duration_raw = fields[10]

                from_code_raw = fields[6]
                to_code_raw = fields[7]
                dep_st = station_map.get(from_code_raw, origin_station)
                arr_st = station_map.get(to_code_raw, destination_station)

                dep_time = f"{date} {dep_time_raw}" if dep_time_raw else ""
                arr_time = f"{date} {arr_time_raw}" if arr_time_raw else ""

                if duration_raw and ":" in str(duration_raw):
                    parts = str(duration_raw).split(":")
                    duration_minutes = int(parts[0]) * 60 + int(parts[1])
                elif dep_time and arr_time:
                    try:
                        dt_dep = datetime.strptime(dep_time, DT_FMT)
                        dt_arr = datetime.strptime(arr_time, DT_FMT)
                        if dt_arr < dt_dep:
                            dt_arr += timedelta(days=1)
                        duration_minutes = int((dt_arr - dt_dep).total_seconds() // 60)
                    except ValueError:
                        continue
                else:
                    continue

                swz_price = self._safe_float(fields[32]) if len(fields) > 32 else 0
                zy_price = self._safe_float(fields[30]) if len(fields) > 30 else 0
                ze_price = self._safe_float(fields[31]) if len(fields) > 31 else 0
                wz_price = self._safe_float(fields[29]) if len(fields) > 29 else 0

                if ze_price > 100:
                    price = ze_price
                    seat_type = "second-class"
                elif zy_price > 100:
                    price = zy_price
                    seat_type = "first-class"
                elif swz_price > 100:
                    price = swz_price
                    seat_type = "business-class"
                elif wz_price > 100:
                    price = wz_price
                    seat_type = "standing"
                else:
                    price = self._estimate_price_by_duration(duration_minutes)
                    seat_type = "second-class"

                train_type = "HSR" if train_no.startswith("G") else "D-series" if train_no.startswith("D") else "C-series"

                option = {
                    "strategy": "synchronized-arrival",
                    "arrival_time": arr_time,
                    "total_minutes": duration_minutes,
                    "total_price": price,
                    "total_transfers": 0,
                    "train_type": train_type,
                    "seat_type": seat_type,
                    "shared_train_ratio": 0.0,
                    "data_source": "12306-direct",
                    "legs": [
                        {
                            "train_no": train_no,
                            "dep_station": dep_st,
                            "arr_station": arr_st,
                            "dep_time": dep_time,
                            "arr_time": arr_time,
                        }
                    ],
                }
                try:
                    CandidateOption.model_validate(option)
                    normalized.append(option)
                except Exception as e:
                    logger.debug("Skipping invalid train %s from 12306-direct: %s", train_no, e)
            except Exception as e:
                logger.debug("Error parsing 12306-direct result: %s", e)
                continue

        return normalized

    @staticmethod
    def _safe_float(value) -> float:
        try:
            if not value or value in ("--", "*", "", "无", "有"):
                return 0.0
            return float(value)
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _estimate_price_by_duration(duration_minutes: int) -> float:
        if duration_minutes <= 0:
            return 0.0
        estimated_km = duration_minutes * 2.5
        price = round(estimated_km * 0.46, 1)
        return max(price, 55.0)

    def _build_station_map_from_codes(self) -> Dict[str, str]:
        codes = self._ensure_station_codes()
        reverse: Dict[str, str] = {}
        for name, telecode in codes.items():
            if telecode not in reverse:
                reverse[telecode] = name
        return reverse


class MCP12306Client(RailwayAPIAdapter):
    """
    Client adapter for external 12306-MCP Server.

    Connects to a running 12306-MCP server (npm package: 12306-mcp)
    to fetch real-time train data.

    Setup:
      1. Install: npx -y 12306-mcp --port 8080
      2. Set env: TRIP_PLANNER_12306_MCP_URL=http://localhost:8080

    This adapter calls the MCP server's HTTP API endpoints:
      - GET /tickets?from=XXX&to=XXX&date=XXX&trainTypes=G
      - GET /interline?from=XXX&to=XXX&date=XXX
    """

    def __init__(
        self,
        endpoint: str = "",
        timeout_seconds: float = 10.0,
        **kwargs,
    ):
        self.endpoint = endpoint or os.getenv("TRIP_PLANNER_12306_MCP_URL", "")
        self.timeout_seconds = timeout_seconds

    def get_provider_name(self) -> str:
        return "12306-mcp"

    def query_trains(
        self,
        origin_station: str,
        destination_station: str,
        date: str,
    ) -> List[dict]:
        if not self.endpoint:
            raise RailwayAPIError(
                "12306-MCP server URL not configured. Set TRIP_PLANNER_12306_MCP_URL or pass endpoint.",
                provider="12306-mcp",
            )

        try:
            import requests as req
        except ImportError:
            raise RailwayAPIError(
                "requests library required for 12306-mcp adapter",
                provider="12306-mcp",
            )

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        url = f"{self.endpoint.rstrip('/')}/tickets"
        params = {
            "from": origin_station,
            "to": destination_station,
            "date": date,
            "trainTypes": "GD",
        }

        try:
            resp = req.get(
                url,
                params=params,
                headers=headers,
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
            data = resp.json()
        except req.Timeout:
            raise RailwayAPIError(
                "12306-MCP server timed out", provider="12306-mcp", status_code=408
            )
        except req.ConnectionError:
            raise RailwayAPIError(
                f"Cannot connect to 12306-MCP server at {self.endpoint}",
                provider="12306-mcp",
            )
        except req.HTTPError as e:
            raise RailwayAPIError(
                f"12306-MCP returned HTTP {e.response.status_code}",
                provider="12306-mcp",
                status_code=e.response.status_code,
            )
        except (json.JSONDecodeError, ValueError):
            raise RailwayAPIError(
                "Invalid JSON from 12306-MCP server", provider="12306-mcp"
            )

        return self._normalize_mcp_response(data, origin_station, destination_station, date)

    def _normalize_mcp_response(
        self,
        data,
        origin_station: str,
        destination_station: str,
        date: str,
    ) -> List[dict]:
        trains = []
        if isinstance(data, dict):
            trains = data.get("data", data.get("trains", data.get("result", [])))
        elif isinstance(data, list):
            trains = data

        if isinstance(trains, dict):
            trains = trains.get("list", trains.get("trains", []))

        normalized = []
        for train in trains:
            try:
                train_no = train.get("trainNo", train.get("station_train_code", train.get("train_no", "")))
                if not train_no:
                    continue

                dep_time_raw = train.get("departTime", train.get("dep_time", train.get("start_time", "")))
                arr_time_raw = train.get("arriveTime", train.get("arr_time", ""))

                if isinstance(dep_time_raw, str) and len(dep_time_raw) >= 5:
                    dep_time = f"{date} {dep_time_raw[:5]}"
                else:
                    dep_time = f"{date} {dep_time_raw}" if dep_time_raw else ""

                if isinstance(arr_time_raw, str) and len(arr_time_raw) >= 5:
                    arr_time = f"{date} {arr_time_raw[:5]}"
                else:
                    arr_time = f"{date} {arr_time_raw}" if arr_time_raw else ""

                duration_minutes = train.get("duration", train.get("runTimeMinutes", 0))
                if isinstance(duration_minutes, str) and ":" in duration_minutes:
                    parts = duration_minutes.split(":")
                    duration_minutes = int(parts[0]) * 60 + int(parts[1])
                duration_minutes = int(duration_minutes) if duration_minutes else 0

                if duration_minutes == 0 and dep_time and arr_time:
                    try:
                        dt_dep = datetime.strptime(dep_time, DT_FMT)
                        dt_arr = datetime.strptime(arr_time, DT_FMT)
                        if dt_arr < dt_dep:
                            dt_arr += timedelta(days=1)
                        duration_minutes = int((dt_arr - dt_dep).total_seconds() // 60)
                    except ValueError:
                        continue

                prices = train.get("prices", {})
                if isinstance(prices, dict):
                    price = float(prices.get("secondClass", prices.get("ze_price", 0)))
                    if price == 0:
                        price = float(prices.get("firstClass", prices.get("zy_price", 0)))
                    seat_type = "second-class" if prices.get("secondClass") or prices.get("ze_price") else "first-class"
                elif isinstance(prices, (int, float, str)):
                    price = float(prices)
                    seat_type = "second-class"
                else:
                    price = 0
                    seat_type = "second-class"

                dep_st = train.get("fromStation", train.get("dep_station", origin_station))
                arr_st = train.get("toStation", train.get("arr_station", destination_station))

                train_type = "HSR" if train_no.startswith("G") else "D-series" if train_no.startswith("D") else "other"

                option = {
                    "strategy": "synchronized-arrival",
                    "arrival_time": arr_time,
                    "total_minutes": duration_minutes,
                    "total_price": price,
                    "total_transfers": 0,
                    "train_type": train_type,
                    "seat_type": seat_type,
                    "shared_train_ratio": 0.0,
                    "data_source": "12306-mcp",
                    "legs": [
                        {
                            "train_no": train_no,
                            "dep_station": dep_st,
                            "arr_station": arr_st,
                            "dep_time": dep_time,
                            "arr_time": arr_time,
                        }
                    ],
                }
                try:
                    CandidateOption.model_validate(option)
                    normalized.append(option)
                except Exception as e:
                    logger.debug("Skipping invalid train %s from 12306-mcp: %s", train_no, e)
            except Exception as e:
                logger.debug("Error normalizing 12306-mcp data: %s", e)
                continue

        return normalized


ADAPTER_REGISTRY: Dict[str, type] = {
    "12306": China12306Adapter,
    "12306-direct": China12306DirectAdapter,
    "12306-mcp": MCP12306Client,
    "ctrip": CtripAdapter,
    "aggregated": AggregatedAdapter,
}

_ADAPTER_CACHE: Dict[str, RailwayAPIAdapter] = {}
_ADAPTER_CACHE_LOCK = threading.Lock()


def get_adapter(provider: str, **kwargs) -> RailwayAPIAdapter:
    cache_key = f"{provider}:{kwargs.get('endpoint', '')}:{kwargs.get('api_key', '')}"
    with _ADAPTER_CACHE_LOCK:
        if cache_key in _ADAPTER_CACHE:
            return _ADAPTER_CACHE[cache_key]
    adapter_cls = ADAPTER_REGISTRY.get(provider)
    if not adapter_cls:
        available = ", ".join(ADAPTER_REGISTRY.keys())
        raise RailwayAPIError(f"Unknown provider '{provider}'. Available: {available}")
    adapter = adapter_cls(**kwargs)
    with _ADAPTER_CACHE_LOCK:
        _ADAPTER_CACHE[cache_key] = adapter
    return adapter


def query_real_trains(
    origin_city: str,
    destination_city: str,
    date: str,
    provider: str = "12306",
    **kwargs,
) -> List[dict]:
    adapter = get_adapter(provider, **kwargs)
    return adapter.query_trains_for_city_pair(origin_city, destination_city, date)
