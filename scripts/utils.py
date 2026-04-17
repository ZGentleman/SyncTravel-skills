import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from models import (
    DT_FMT,
    ALL_KNOWN_STATIONS,
    HSR_PRIMARY_STATION,
    STATION_ALIASES,
    WeightProfile,
    WEIGHT_PROFILES,
    is_known_station,
    validate_station_for_city,
)


CITY_NAME_MAP: Dict[str, str] = {
    "广州": "Guangzhou",
    "武汉": "Wuhan",
    "北京": "Beijing",
    "上海": "Shanghai",
    "深圳": "Shenzhen",
    "郑州": "Zhengzhou",
    "长沙": "Changsha",
    "南京": "Nanjing",
    "成都": "Chengdu",
    "杭州": "Hangzhou",
    "石家庄": "Shijiazhuang",
    "西安": "Xi'an",
    "济南": "Jinan",
    "合肥": "Hefei",
    "南昌": "Nanchang",
    "福州": "Fuzhou",
    "昆明": "Kunming",
    "贵阳": "Guiyang",
    "重庆": "Chongqing",
    "天津": "Tianjin",
    "苏州": "Suzhou",
    "青岛": "Qingdao",
    "大连": "Dalian",
    "沈阳": "Shenyang",
    "哈尔滨": "Harbin",
    "长春": "Changchun",
    "太原": "Taiyuan",
    "兰州": "Lanzhou",
    "徐州": "Xuzhou",
    "无锡": "Wuxi",
    "guangzhou": "Guangzhou",
    "wuhan": "Wuhan",
    "beijing": "Beijing",
    "shanghai": "Shanghai",
    "shenzhen": "Shenzhen",
    "zhengzhou": "Zhengzhou",
    "changsha": "Changsha",
    "nanjing": "Nanjing",
    "chengdu": "Chengdu",
    "hangzhou": "Hangzhou",
    "shijiazhuang": "Shijiazhuang",
    "xian": "Xi'an",
    "jinan": "Jinan",
    "hefei": "Hefei",
    "nanchang": "Nanchang",
    "fuzhou": "Fuzhou",
    "kunming": "Kunming",
    "guiyang": "Guiyang",
    "chongqing": "Chongqing",
    "tianjin": "Tianjin",
}


def resolve_city_name(raw: str) -> str:
    return CITY_NAME_MAP.get(raw, CITY_NAME_MAP.get(raw.lower(), raw))


def resolve_station_for_city(city: str) -> Tuple[str, List[str]]:
    if city in HSR_PRIMARY_STATION:
        primary = HSR_PRIMARY_STATION[city]
        all_stations = STATION_ALIASES.get(city, [primary])
        return primary, all_stations
    all_stations = STATION_ALIASES.get(city, [city])
    return all_stations[0], all_stations


def validate_city_has_hsr(city: str) -> Tuple[bool, str]:
    if city in STATION_ALIASES:
        return True, ""
    return False, f"City '{city}' is not in the verified high-speed rail station database. Available cities: {', '.join(sorted(STATION_ALIASES.keys())[:10])}... (and more)"


def resolve_city_with_validation(raw: str) -> Tuple[str, List[str]]:
    city = resolve_city_name(raw)
    is_valid, msg = validate_city_has_hsr(city)
    if not is_valid:
        return city, [f"[WARNING] {msg} Station data may not be accurate for this city."]
    primary, all_stations = resolve_station_for_city(city)
    warnings = []
    if city not in STATION_ALIASES:
        warnings.append(f"[WARNING] City '{city}' not in verified HSR database. Station assignments may be inaccurate.")
    return city, warnings


def _build_timeline(legs: list) -> str:
    if not legs:
        return ""
    try:
        segments = []
        for leg in legs:
            dep_t = leg.get("dep_time", "")[11:] if len(leg.get("dep_time", "")) > 11 else ""
            arr_t = leg.get("arr_time", "")[11:] if len(leg.get("arr_time", "")) > 11 else ""
            train = leg.get("train_no", "")
            dep_st = leg.get("dep_station", "")
            arr_st = leg.get("arr_station", "")
            if dep_t and arr_t:
                dep_min = int(dep_t[:2]) * 60 + int(dep_t[3:5])
                arr_min = int(arr_t[:2]) * 60 + int(arr_t[3:5])
                if arr_min < dep_min:
                    arr_min += 1440
                duration = arr_min - dep_min
                bar_len = max(1, duration // 10)
                bar = "=" * bar_len
                segments.append(f"{dep_t} {dep_st} {bar}{train}{bar} {arr_st} {arr_t}")
            else:
                segments.append(f"{train}: {dep_st}->{arr_st}")
        return " | ".join(segments)
    except Exception:
        return ""


def to_markdown(result: dict) -> str:
    lines: list[str] = []
    plans = result.get("top_plans", [])

    route_analysis = result.get("route_analysis")
    if route_analysis:
        warnings = route_analysis.get("warnings", [])
        if warnings:
            lines.append("## [!] Data Source Notice")
            for w in warnings:
                lines.append(f"- {w}")
            lines.append("")

    if not plans:
        lines.append("## No Feasible Itinerary")
        lines.append(result.get("message", "No plans found under the given constraints."))
        suggestions = result.get("relaxation_suggestions", [])
        if suggestions:
            lines.append("")
            lines.append("### Suggested Relaxations")
            for s in suggestions:
                desc = s.get("description", "")
                count = s.get("feasible_count_after", "?")
                lines.append(f"- **{desc}** (可产生 {count} 个方案)")
        return "\n".join(lines)

    meta = result.get("meta", {})
    lines.append(f"## Coordinated Trip Plan (Top {len(plans)})")
    data_source = meta.get("data_source", "mock")
    reliability = meta.get("data_source_reliability", "low")
    if data_source == "mock":
        lines.append(f"[Data] Data source: **Mock** (reliability: {reliability}) -- For real-time data, integrate 12306/Ctrip API")
    else:
        lines.append(f"[Data] Data source: **{data_source}** (reliability: {reliability})")
    lines.append(
        f"Total feasible: {result.get('total_feasible', 0)} | "
        f"Planning time: {meta.get('planning_time_seconds', 0):.2f}s"
    )
    if meta.get("timed_out"):
        lines.append("[!] Planning timed out, results may be incomplete")
    lines.append("")

    for idx, plan in enumerate(plans, 1):
        score = plan.get("score", 0)
        strategies = " + ".join(plan.get("strategy_mix", []))
        spread = plan.get("arrival_spread_minutes", 0)
        total_min = plan.get("total_minutes", 0)
        total_price = plan.get("total_price", 0)
        total_xfer = plan.get("total_transfers", 0)
        reasoning = plan.get("reasoning", "")

        lines.append(f"### Option {idx} -- {strategies} -- Score: {score:.1f}")
        if plan.get("is_best_compromise"):
            lines.append("[Best] **Best Compromise** -- 综合最优折中方案")
        lines.append(f"- Total travel time: {total_min} min | Total price: CNY {total_price:.0f}")
        lines.append(f"- Transfers: {total_xfer} | Arrival spread: {spread} min")
        if reasoning:
            lines.append(f"- **Why**: {reasoning}")

        plan_analysis = plan.get("route_analysis")
        if plan_analysis and plan_analysis.get("issues"):
            for issue in plan_analysis["issues"]:
                severity = issue.get("severity", "warning")
                icon = "[X]" if severity == "error" else "[!]"
                lines.append(f"- {icon} {issue.get('message', '')}")

        lines.append("")

        for opt in plan.get("options", []):
            name = opt.get("traveler", "?")
            strategy = opt.get("strategy", "")
            lines.append(f"**{name}** ({strategy}):")
            for li, leg in enumerate(opt.get("legs", []), 1):
                train = leg.get("train_no", "?")
                dep_st = leg.get("dep_station", "?")
                dep_t = leg.get("dep_time", "?")
                arr_st = leg.get("arr_station", "?")
                arr_t = leg.get("arr_time", "?")
                lines.append(f"  {li}. {train}: {dep_st} {dep_t[11:]} -> {arr_st} {arr_t[11:]}")
            timeline = _build_timeline(opt.get("legs", []))
            if timeline:
                lines.append(f"  [Timeline] {timeline}")
            lines.append("")

    if meta:
        lines.append("---")
        lines.append(
            f"Generated at: {meta.get('generated_at', 'N/A')} | "
            f"Combos: {meta.get('feasible_combinations', 0)}/{meta.get('total_combinations', 0)}"
        )

    return "\n".join(lines)


def build_quick_payload(
    origins: str,
    destination: str,
    date: str,
    departure_time_range: str = "07:00-15:00",
    max_transfers: int = 2,
    arrival_sync_minutes: int = 60,
    weight_profile: str = "balanced",
) -> dict:
    try:
        profile = WeightProfile(weight_profile)
        weights = dict(WEIGHT_PROFILES[profile])
    except ValueError:
        weights = dict(WEIGHT_PROFILES[WeightProfile.BALANCED])

    raw_cities = [c.strip() for c in origins.split(",")]
    cities = [resolve_city_name(c) for c in raw_cities]
    destination = resolve_city_name(destination)

    unverified = [c for c in cities + [destination] if c not in STATION_ALIASES]
    if unverified:
        import logging
        logger = logging.getLogger("trip_planner.utils")
        logger.warning(
            "Unverified cities in quick plan: %s. These cities are not in the HSR station database. "
            "Station assignments may be inaccurate. Verified cities: %s",
            ", ".join(unverified),
            ", ".join(sorted(STATION_ALIASES.keys())[:15]) + "...",
        )

    dep_parts = departure_time_range.split("-")
    if len(dep_parts) != 2:
        raise ValueError(f"Invalid departure_time_range format: {departure_time_range}, expected HH:MM-HH:MM")
    dep_start = f"{date} {dep_parts[0].strip()}"
    dep_end = f"{date} {dep_parts[1].strip()}"

    datetime.strptime(dep_start, DT_FMT)
    datetime.strptime(dep_end, DT_FMT)

    travelers = []
    for i, city in enumerate(cities):
        travelers.append({
            "name": f"traveler_{i+1}",
            "origin": city,
            "earliest_departure": dep_start,
            "latest_departure": dep_end,
            "priority_weight": 1.0,
            "preferences": {},
        })

    return {
        "travelers": travelers,
        "destination": destination,
        "date": date,
        "constraints": {
            "max_transfers": max_transfers,
            "max_arrival_time_diff_minutes": arrival_sync_minutes,
            "must_train_types": ["HSR"],
            "accept_standing": False,
            "allowed_transfer_hubs": [],
        },
        "weights": weights,
    }


def parse_natural_language_request(user_message: str) -> dict:
    msg = user_message

    cities_found = []
    all_cities = list(STATION_ALIASES.keys())
    for city in all_cities:
        if city.lower() in msg.lower():
            cities_found.append(city)

    for cn_name, en_name in CITY_NAME_MAP.items():
        if cn_name in msg and en_name not in cities_found:
            if en_name not in cities_found:
                cities_found.append(en_name)

    date_match = re.search(r"(\d{4}[-/]\d{2}[-/]\d{2})", msg)
    date_str = date_match.group(1).replace("/", "-") if date_match else ""
    if not date_str:
        weekday_map = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
        en_weekday_map = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
                          "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
        relative_date_match = re.search(r"(明天|后天|大后天|这周末|下周[一二三四五六日天]?|周[一二三四五六日天]|星期[一二三四五六日天])", msg)
        if relative_date_match:
            from datetime import date as date_type
            today = date_type.today()
            keyword = relative_date_match.group(1)
            offset_map = {"明天": 1, "后天": 2, "大后天": 3}
            if keyword in offset_map:
                target = today + timedelta(days=offset_map[keyword])
                date_str = target.strftime("%Y-%m-%d")
            elif keyword == "这周末":
                days_until_saturday = (5 - today.weekday()) % 7
                if days_until_saturday == 0:
                    days_until_saturday = 7
                target = today + timedelta(days=days_until_saturday)
                date_str = target.strftime("%Y-%m-%d")
            elif keyword.startswith("下周"):
                if len(keyword) > 2:
                    cn_day = keyword[-1]
                else:
                    cn_day = "一"
                if cn_day in weekday_map:
                    target_weekday = weekday_map[cn_day]
                    days_ahead = (target_weekday - today.weekday()) % 7 + 7
                    target = today + timedelta(days=days_ahead)
                    date_str = target.strftime("%Y-%m-%d")
            elif keyword.startswith("周") or keyword.startswith("星期"):
                cn_day = keyword[-1]
                if cn_day in weekday_map:
                    target_weekday = weekday_map[cn_day]
                    days_ahead = (target_weekday - today.weekday()) % 7
                    if days_ahead == 0:
                        days_ahead = 7
                    target = today + timedelta(days=days_ahead)
                    date_str = target.strftime("%Y-%m-%d")
        if not date_str:
            from datetime import date as date_type
            today = date_type.today()
            msg_lower = msg.lower()
            en_relative_match = re.search(r"\b(tomorrow|day after tomorrow|next (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|week|mon|tue|wed|thu|fri|sat|sun))\b", msg_lower)
            if en_relative_match:
                kw = en_relative_match.group(1)
                if kw == "tomorrow":
                    date_str = (today + timedelta(days=1)).strftime("%Y-%m-%d")
                elif kw == "day after tomorrow":
                    date_str = (today + timedelta(days=2)).strftime("%Y-%m-%d")
                elif kw.startswith("next "):
                    day_name = kw[5:]
                    if day_name == "week":
                        days_until_monday = (0 - today.weekday()) % 7
                        if days_until_monday == 0:
                            days_until_monday = 7
                        date_str = (today + timedelta(days=days_until_monday)).strftime("%Y-%m-%d")
                    elif day_name in en_weekday_map:
                        target_wd = en_weekday_map[day_name]
                        days_ahead = (target_wd - today.weekday()) % 7
                        if days_ahead == 0:
                            days_ahead = 7
                        date_str = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    destination_patterns = [
        r"去(.+?)(?:，|,|\s|$)",
        r"到(.+?)(?:，|,|\s|$)",
        r"前往(.+?)(?:，|,|\s|$)",
        r"飞(.+?)(?:，|,|\s|$)",
        r"go to (\w+)",
        r"travel to (\w+)",
        r"head(?:ing)? to (\w+)",
    ]
    explicit_destination = ""
    for pattern in destination_patterns:
        m = re.search(pattern, msg)
        if m:
            dest_raw = m.group(1).strip()
            resolved = resolve_city_name(dest_raw)
            if resolved in STATION_ALIASES:
                explicit_destination = resolved
                if explicit_destination not in cities_found:
                    cities_found.append(explicit_destination)
                break

    origin_patterns = [
        r"从(.+?)(?:和|与|、|,|以及)(.+?)(?:出|去|到|飞|出发)",
        r"我在(.+?)，(?:他|她|朋友|同事|同学|女友|男朋友|女朋友|家人|老婆|老公)在(.+?)(?:，|$)",
        r"(.+?)和(.+?)(?:一起|出发|去|到)",
    ]
    single_origin_patterns = [
        r"我[从在](.+?)(?:出发|去|到|飞|坐车|走)",
        r"从(.+?)(?:出发|走|启程|坐车)",
    ]
    explicit_origins = []
    for pattern in origin_patterns:
        m = re.search(pattern, msg)
        if m:
            for g in m.groups():
                resolved = resolve_city_name(g.strip())
                if resolved in STATION_ALIASES and resolved not in explicit_origins:
                    explicit_origins.append(resolved)
            break
    if not explicit_origins:
        for pattern in single_origin_patterns:
            m = re.search(pattern, msg)
            if m:
                resolved = resolve_city_name(m.group(1).strip())
                if resolved in STATION_ALIASES and resolved not in explicit_origins:
                    explicit_origins.append(resolved)
                break

    sync_keywords = ["同步", "同时", "一起到", "差不多到", "同时到", "一起到", "synchronized", "same time", "together"]
    same_train_keywords = ["同车", "同一趟", "顺路", "same train", "same carriage", "一起坐"]
    budget_keywords = ["便宜", "省钱", "budget", "cheap", "性价比"]
    speed_keywords = ["最快", "赶时间", "急", "fastest", "quickest", "urgent", "尽快"]
    comfort_keywords = ["舒服", "舒适", "少换乘", "少转车", "comfortable", "no transfer"]

    weight_profile = "balanced"
    if any(k in msg.lower() for k in sync_keywords):
        weight_profile = "sync-first"
    elif any(k in msg.lower() for k in same_train_keywords):
        weight_profile = "balanced"
    elif any(k in msg.lower() for k in comfort_keywords):
        weight_profile = "comfort-first"
    elif any(k in msg.lower() for k in budget_keywords):
        weight_profile = "budget-first"
    elif any(k in msg.lower() for k in speed_keywords):
        weight_profile = "speed-first"

    if explicit_destination and explicit_destination in cities_found:
        destination = explicit_destination
        origins = [c for c in cities_found if c != destination]
    elif explicit_origins:
        origins = explicit_origins
        remaining = [c for c in cities_found if c not in explicit_origins]
        destination = remaining[-1] if remaining else ""
    else:
        destination = cities_found[-1] if len(cities_found) >= 2 else ""
        origins = cities_found[:-1] if len(cities_found) >= 2 else cities_found

    station_info = {}
    for city in cities_found:
        primary, all_stations = resolve_station_for_city(city)
        station_info[city] = {
            "primary_hsr_station": primary,
            "all_stations": all_stations,
        }

    template = {
        "inferred": {
            "origins": origins,
            "destination": destination,
            "date": date_str,
            "weight_profile": weight_profile,
            "station_info": station_info,
        },
        "missing_fields": [],
        "suggested_next_action": "",
        "validation": {
            "all_cities_verified": all(c in STATION_ALIASES for c in cities_found),
            "unverified_cities": [c for c in cities_found if c not in STATION_ALIASES],
            "data_source": "mock",
            "data_source_reliability": "low",
            "note": "Station assignments are based on verified HSR database. For real-time data, integrate 12306/Ctrip API.",
            "warning": "DO NOT fabricate station names. Only use stations from station_info. If a city is unverified, ask the user to confirm.",
        },
        "ai_skill_boundary": {
            "ai_responsibilities": [
                "Parse natural language to extract cities, dates, preferences",
                "Ask user for missing information (date, city names)",
                "Present results in a user-friendly format",
                "Translate between Chinese and English city names",
            ],
            "skill_responsibilities": [
                "Validate city and station names against verified database",
                "Generate candidate train options (mock or real API)",
                "Compute strategy-specific itineraries",
                "Score and rank plans with weighted optimization",
                "Validate constraints and suggest relaxations",
                "Analyze route validity and data reliability",
            ],
            "ai_must_not": [
                "Invent or fabricate station names not in the database",
                "Override station validation results",
                "Skip the validation step before presenting station data to users",
                "Modify scoring weights or strategy logic",
            ],
        },
    }

    if not origins:
        template["missing_fields"].append("origin cities")
    if not destination:
        template["missing_fields"].append("destination city")
    if not date_str:
        template["missing_fields"].append("travel date")

    if template["missing_fields"]:
        template["suggested_next_action"] = f"Ask user for: {', '.join(template['missing_fields'])}"
    else:
        template["suggested_next_action"] = "Call trip_planner_quick_plan with inferred fields"

    return template
