import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from models import ResponseFormat, TripPlannerInput, WeightProfile, WEIGHT_PROFILES
from plan_trips import plan_trip_data
from provider_layer import FileTTLCache, ProviderError, get_data_source_for_provider, resolve_provider
from utils import build_quick_payload, parse_natural_language_request, resolve_city_with_validation, to_markdown

try:
    from mcp.server.fastmcp import FastMCP
except Exception as exc:
    raise RuntimeError("Missing dependency `mcp`. Install with: pip install mcp") from exc

logger = logging.getLogger("trip_planner.mcp")

mcp = FastMCP("trip_planner_mcp")
cache = FileTTLCache(cache_dir=".cache/trip-planner", ttl_seconds=600)


class TripPlannerPlanInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    travelers: List[Dict[str, Any]] = Field(
        ...,
        description="List of travelers with name, origin, earliest_departure, latest_departure, priority_weight, preferences",
        min_length=1,
    )
    destination: str = Field(..., description="Common destination city name", min_length=1)
    date: str = Field(..., description="Travel date in YYYY-MM-DD format", min_length=1)
    constraints: Dict[str, Any] = Field(
        ...,
        description="Constraints: max_transfers, max_arrival_time_diff_minutes, latest_arrival, must_train_types, accept_standing, allowed_transfer_hubs",
    )
    weights: Dict[str, float] = Field(
        ...,
        description="Scoring weights: travel_time, price, transfer_penalty, arrival_sync, comfort_penalty, priority_satisfaction, same_train_bonus (all > 0)",
    )
    candidate_options: Dict[str, List[Dict[str, Any]]] = Field(
        ...,
        description="Candidate train options keyed by traveler name. Each option has strategy, arrival_time, total_minutes, total_price, total_transfers, train_type, seat_type, legs, etc.",
    )
    topk: int = Field(default=3, description="Number of top plans to return", ge=1, le=20)
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: json for programmatic use, markdown for human readability",
    )


class TripPlannerFileInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    input_path: str = Field(..., description="Absolute or relative path to input JSON file", min_length=1)
    topk: int = Field(default=3, description="Number of top plans to return", ge=1, le=20)
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: json for programmatic use, markdown for human readability",
    )


class TripPlannerProviderInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    travelers: List[Dict[str, Any]] = Field(
        ...,
        description="List of travelers with name, origin, earliest_departure, latest_departure",
        min_length=1,
    )
    destination: str = Field(..., description="Common destination city name", min_length=1)
    date: str = Field(..., description="Travel date in YYYY-MM-DD format", min_length=1)
    constraints: Dict[str, Any] = Field(..., description="Constraints object")
    weights: Dict[str, float] = Field(..., description="Scoring weights object")
    provider_name: str = Field(
        default="auto",
        description="Provider to resolve candidate options: 'auto' (recommended, tries 12306-direct->12306-mcp->estimation), '12306-direct' (free official API), '12306-mcp' (external MCP server), '12306' (proxy API), 'ctrip' (proxy API), 'mock' (estimation only)",
    )
    api_endpoint: str = Field(default="", description="External API endpoint URL (required when provider_name=api)")
    api_key: str = Field(default="", description="API key for external provider (or set TRIP_PLANNER_API_KEY env var)")
    topk: int = Field(default=3, description="Number of top plans to return", ge=1, le=20)
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: json for programmatic use, markdown for human readability",
    )


class QuickPlanInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    origins: str = Field(
        ...,
        description="Comma-separated origin cities (supports Chinese names like 广州,武汉), e.g. 'Guangzhou,Wuhan' or '广州,武汉'",
        min_length=1,
    )
    destination: str = Field(
        ...,
        description="Destination city name (supports Chinese names like 北京)",
        min_length=1,
    )
    date: str = Field(..., description="Travel date in YYYY-MM-DD format", min_length=1)
    departure_time_range: str = Field(
        default="07:00-15:00",
        description="Acceptable departure time range in HH:MM-HH:MM format",
    )
    max_transfers: int = Field(default=2, description="Max transfers allowed per traveler", ge=0, le=5)
    arrival_sync_minutes: int = Field(
        default=60,
        description="Max arrival time gap between travelers in minutes",
        ge=0,
    )
    weight_profile: str = Field(
        default="balanced",
        description="Weight preset: balanced, speed-first, comfort-first, budget-first, sync-first",
    )
    data_source: str = Field(
        default="auto",
        description="Data provider: auto (recommended, tries 12306-direct->12306-mcp->estimation), 12306-direct (free official API, no key needed), 12306-mcp (external MCP server), 12306 (proxy API, needs key), ctrip (proxy API, needs key), mock (estimation only, for dev).",
    )
    topk: int = Field(default=3, description="Number of top plans to return", ge=1, le=20)
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: json for programmatic use, markdown for human readability",
    )


class ExplainInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    plan_json: str = Field(
        ...,
        description="A ranked plan JSON string (output from any trip_planner tool)",
        min_length=1,
    )
    audience: str = Field(
        default="general",
        description="Explanation style: general, technical, budget-conscious",
    )


class ParseRequestInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    user_message: str = Field(
        ...,
        description="User's natural language trip request (supports Chinese)",
        min_length=1,
    )


def _format_result(result: dict, response_format: ResponseFormat) -> str:
    if response_format == ResponseFormat.MARKDOWN:
        return to_markdown(result)
    return json.dumps(result, ensure_ascii=False, indent=2)


def _safe_plan(payload: dict, topk: int, input_name: str, data_source: str = "mock") -> dict:
    try:
        return plan_trip_data(data=payload, topk=topk, input_name=input_name, data_source=data_source)
    except ProviderError as err:
        raise err
    except Exception as err:
        logger.error("Planning failed: %s", type(err).__name__)
        raise ProviderError("Trip planning failed due to invalid input or internal error") from err


@mcp.tool(
    name="trip_planner_plan",
    annotations={
        "title": "Plan Multi-User Coordinated Trip",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def trip_planner_plan(params: TripPlannerPlanInput) -> str:
    """
    Plan multi-origin coordinated train trips for N travelers with constraints and scoring.
    Accepts full trip payload including candidate_options and returns ranked itinerary plans.
    Supports both JSON and Markdown output formats.
    """
    payload = params.model_dump(exclude={"topk", "response_format"})
    result = _safe_plan(payload=payload, topk=params.topk, input_name="mcp_payload", data_source="user_provided")
    return _format_result(result, params.response_format)


@mcp.tool(
    name="trip_planner_plan_from_file",
    annotations={
        "title": "Plan Trip from JSON File",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def trip_planner_plan_from_file(params: TripPlannerFileInput) -> str:
    """
    Load planner payload from a JSON file and return ranked coordinated trip plans.
    Use when the trip configuration is stored in a file on disk.
    """
    path = Path(params.input_path)
    if not path.exists():
        raise ProviderError(f"Input file not found: {params.input_path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    result = _safe_plan(payload=data, topk=params.topk, input_name=str(path), data_source="user_provided")
    return _format_result(result, params.response_format)


@mcp.tool(
    name="trip_planner_plan_with_provider",
    annotations={
        "title": "Plan Trip with Auto-Resolved Options",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def trip_planner_plan_with_provider(params: TripPlannerProviderInput) -> str:
    """
    Resolve candidate train options from a provider (auto, 12306-direct, 12306-mcp, 12306, ctrip, mock, or external API), then rank multi-user coordinated trip plans.
    Provider 'auto' (recommended) tries 12306-direct(free,real-time) -> 12306-mcp -> distance-estimation.
    Provider '12306-direct' queries the official 12306 public API directly (free, no API key needed).
    Provider '12306-mcp' calls an external 12306-MCP server (set TRIP_PLANNER_12306_MCP_URL env var).
    Provider 'mock' uses distance-based estimation only (for development).
    Provider 'api' calls a custom external ticket endpoint.
    """
    work = params.model_dump(exclude={"topk", "response_format", "provider_name", "api_endpoint", "api_key"})
    work["candidate_options"] = resolve_provider(
        provider_name=params.provider_name,
        cache=cache,
        payload=work,
        api_endpoint=params.api_endpoint,
        api_key=params.api_key,
    )
    data_source = get_data_source_for_provider(params.provider_name)
    result = _safe_plan(payload=work, topk=params.topk, input_name="mcp_provider_payload", data_source=data_source)
    return _format_result(result, params.response_format)


@mcp.tool(
    name="trip_planner_quick_plan",
    annotations={
        "title": "Quick Plan for Simple Multi-Origin Trips",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def trip_planner_quick_plan(params: QuickPlanInput) -> str:
    """
    Quick plan for simple multi-origin trips. Only requires origin cities, destination, and date.
    Auto-resolves candidate options from real-time data sources (12306 direct API by default).
    Supports Chinese city names (广州, 武汉, 北京, etc.).
    Use weight_profile to control optimization focus: balanced, speed-first, comfort-first, budget-first, sync-first.
    Validates city names against verified HSR station database and warns about unverified cities.
    Data source priority (data_source=auto): 12306-direct(free,real-time) -> 12306-mcp -> distance-estimation.
    """
    payload = build_quick_payload(
        origins=params.origins,
        destination=params.destination,
        date=params.date,
        departure_time_range=params.departure_time_range,
        max_transfers=params.max_transfers,
        arrival_sync_minutes=params.arrival_sync_minutes,
        weight_profile=params.weight_profile,
    )
    city_warnings = []
    for t in payload.get("travelers", []):
        city, warns = resolve_city_with_validation(t["origin"])
        city_warnings.extend(warns)
    dest_city, dest_warns = resolve_city_with_validation(params.destination)
    city_warnings.extend(dest_warns)

    payload["candidate_options"] = resolve_provider(
        provider_name=params.data_source,
        cache=cache,
        payload=payload,
    )
    result = _safe_plan(payload=payload, topk=params.topk, input_name="quick_plan", data_source=params.data_source)
    if city_warnings and result.get("route_analysis"):
        existing_warnings = result["route_analysis"].get("warnings", [])
        result["route_analysis"]["warnings"] = existing_warnings + city_warnings
    return _format_result(result, params.response_format)


@mcp.tool(
    name="trip_planner_explain",
    annotations={
        "title": "Explain a Ranked Trip Plan",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def trip_planner_explain(params: ExplainInput) -> str:
    """
    Generate a human-friendly explanation of a ranked trip plan.
    Takes a plan JSON (output from any trip_planner tool) and returns a natural language summary.
    Use audience parameter to adjust explanation style: general, technical, budget-conscious.
    """
    try:
        plan = json.loads(params.plan_json)
    except json.JSONDecodeError:
        raise ProviderError("Invalid JSON in plan_json parameter")

    lines: list[str] = []
    plans = plan.get("top_plans", [])
    if not plans:
        msg = plan.get("message", "No feasible plans found.")
        lines.append(f"No feasible itinerary found. {msg}")
        suggestions = plan.get("relaxation_suggestions", [])
        if suggestions:
            lines.append("")
            lines.append("You could try:")
            for s in suggestions:
                lines.append(f"  - {s['description']}")
        return "\n".join(lines)

    total_feasible = plan.get("total_feasible", 0)
    lines.append(f"Found {total_feasible} feasible plan(s). Here are the top recommendations:")
    lines.append("")

    for idx, p in enumerate(plans, 1):
        score = p.get("score", 0)
        strategies = " + ".join(p.get("strategy_mix", []))
        spread = p.get("arrival_spread_minutes", 0)
        total_price = p.get("total_price", 0)
        total_xfer = p.get("total_transfers", 0)
        reasoning = p.get("reasoning", "")

        if params.audience == "budget-conscious":
            compromise_tag = " [BEST COMPROMISE]" if p.get("is_best_compromise") else ""
            lines.append(f"**Option {idx}** (CNY {total_price:.0f}, {total_xfer} transfer(s)){compromise_tag}: {reasoning}")
        elif params.audience == "technical":
            compromise_tag = " *" if p.get("is_best_compromise") else ""
            lines.append(f"**Option {idx}** [score={score}, strategy={strategies}, spread={spread}min]{compromise_tag}: {reasoning}")
        else:
            compromise_tag = " [BEST]" if p.get("is_best_compromise") else ""
            lines.append(f"**Option {idx}**{compromise_tag}: {reasoning}")

        for opt in p.get("options", []):
            name = opt.get("traveler", "?")
            arrival = opt.get("arrival_time", "?")
            strategy = opt.get("strategy", "")
            legs = opt.get("legs", [])
            if legs:
                first = legs[0]
                last = legs[-1]
                dep_short = first.get("dep_time", "?")[11:]
                arr_short = last.get("arr_time", "?")[11:]
                train_nos = " -> ".join(leg.get("train_no", "?") for leg in legs)
                lines.append(f"  - {name}: {train_nos}, depart {dep_short}, arrive {arr_short} ({strategy})")
            else:
                lines.append(f"  - {name}: arrive {arrival} ({strategy})")
        lines.append("")

    return "\n".join(lines)


@mcp.tool(
    name="trip_planner_parse_request",
    annotations={
        "title": "Parse Natural Language Trip Request",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def trip_planner_parse_request(params: ParseRequestInput) -> str:
    """
    Parse a natural language trip request into a structured JSON template for trip_planner tools.
    Extracts cities, dates, and preferences from user messages. Supports Chinese city names.
    Validates extracted cities against the verified HSR station database to prevent hallucinated station data.
    Returns a JSON template with inferred fields that the agent should refine before calling other tools.
    """
    template = parse_natural_language_request(params.user_message)
    return json.dumps(template, ensure_ascii=False, indent=2)


@mcp.tool(
    name="trip_planner_list_cities",
    annotations={
        "title": "List Supported Cities and Stations",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def trip_planner_list_cities() -> str:
    """
    List all cities and their HSR stations supported by the trip planner.
    Returns a mapping of city names to their verified high-speed rail stations.
    Use this to check if a city is supported before planning, or to find the correct station names.
    """
    from models import STATION_ALIASES, HSR_PRIMARY_STATION
    result = {}
    for city, stations in sorted(STATION_ALIASES.items()):
        primary = HSR_PRIMARY_STATION.get(city, stations[0] if stations else "")
        result[city] = {
            "primary_station": primary,
            "all_stations": stations,
        }
    return json.dumps(result, ensure_ascii=False, indent=2)


class ValidateStationsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    stations: List[str] = Field(
        ...,
        description="List of station names to validate against the verified HSR station database",
        min_length=1,
    )
    city: str = Field(
        default="",
        description="Optional city name to check if stations belong to this city. If provided, validates station-city assignment.",
    )


@mcp.tool(
    name="trip_planner_validate_stations",
    annotations={
        "title": "Validate Station Names Against Verified Database",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def trip_planner_validate_stations(params: ValidateStationsInput) -> str:
    """
    Validate station names against the verified HSR station database.
    Checks if each station exists in the database and optionally if it belongs to a specified city.
    Use this before planning to prevent hallucinated or incorrect station names from AI.
    Returns validation results with details about each station.
    """
    from models import validate_station_for_city, is_known_station, city_to_stations, HSR_PRIMARY_STATION
    results = []
    for station in params.stations:
        known = is_known_station(station)
        entry = {
            "station": station,
            "is_known": known,
        }
        if known:
            matching_cities = []
            from models import STATION_ALIASES
            for city_key, stations_list in STATION_ALIASES.items():
                if station in stations_list:
                    matching_cities.append(city_key)
            entry["matching_cities"] = matching_cities
            entry["primary_station_of"] = [c for c in matching_cities if HSR_PRIMARY_STATION.get(c) == station]
        if params.city:
            valid, msg = validate_station_for_city(station, params.city)
            entry["belongs_to_city"] = valid
            entry["validation_message"] = msg
        if not known:
            entry["warning"] = f"Station '{station}' is NOT in the verified HSR station database. Do NOT use this station name in planning. Use trip_planner_list_cities to find correct station names."
        results.append(entry)
    all_known = all(r["is_known"] for r in results)
    summary = {
        "all_valid": all_known,
        "total_checked": len(results),
        "total_known": sum(1 for r in results if r["is_known"]),
        "total_unknown": sum(1 for r in results if not r["is_known"]),
        "results": results,
    }
    if not all_known:
        summary["action_required"] = "Some stations are not in the verified database. Replace unknown stations with verified ones from trip_planner_list_cities before proceeding."
    return json.dumps(summary, ensure_ascii=False, indent=2)
