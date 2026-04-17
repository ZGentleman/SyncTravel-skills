import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from models import ResponseFormat, WeightProfile, WEIGHT_PROFILES
from plan_trips import plan_trip_data
from provider_layer import FileTTLCache, ProviderError, get_data_source_for_provider, resolve_provider
from utils import build_quick_payload, resolve_city_with_validation, to_markdown

logger = logging.getLogger("trip_planner.api")


class PlanRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    payload: Dict[str, Any] = Field(..., description="Full trip planner input payload")
    topk: int = Field(default=3, description="Number of top plans to return", ge=1, le=20)
    provider_name: str = Field(default="auto", description="Provider: 'auto', '12306-direct', '12306-mcp', '12306', 'ctrip', 'mock', or 'api'")
    api_endpoint: str = Field(default="", description="External API endpoint URL")
    api_key: str = Field(default="", description="API key for external provider")
    use_provider_when_missing_candidates: bool = Field(
        default=True, description="Auto-resolve candidate options when not in payload"
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.JSON,
        description="Output format: json or markdown",
    )


class QuickPlanRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    origins: str = Field(..., description="Comma-separated origin cities (supports Chinese)", min_length=1)
    destination: str = Field(..., description="Destination city name (supports Chinese)", min_length=1)
    date: str = Field(..., description="Travel date in YYYY-MM-DD format", min_length=1)
    departure_time_range: str = Field(default="07:00-15:00", description="HH:MM-HH:MM")
    max_transfers: int = Field(default=2, ge=0, le=5)
    arrival_sync_minutes: int = Field(default=60, ge=0)
    weight_profile: str = Field(default="balanced")
    data_source: str = Field(default="auto", description="Data provider: auto, 12306-direct, 12306-mcp, 12306, ctrip, mock")
    topk: int = Field(default=3, ge=1, le=20)
    response_format: ResponseFormat = Field(default=ResponseFormat.JSON)


class HealthResponse(BaseModel):
    status: str


app = FastAPI(
    title="Multi-user Trip Planner API",
    version="2.1.0",
    description="Plan and rank coordinated rail itineraries for N travelers from multiple origins to one destination.",
)
cache = FileTTLCache(
    cache_dir=os.getenv("TRIP_PLANNER_CACHE_DIR", ".cache/trip-planner"),
    ttl_seconds=int(os.getenv("TRIP_PLANNER_CACHE_TTL_SECONDS", "180")),
)


@app.get("/health", response_model=HealthResponse)
def health() -> dict:
    return {"status": "ok"}


@app.post("/plan")
def plan(req: PlanRequest):
    try:
        work = dict(req.payload)
        if req.use_provider_when_missing_candidates and not work.get("candidate_options"):
            work["candidate_options"] = resolve_provider(
                provider_name=req.provider_name,
                cache=cache,
                payload=work,
                api_endpoint=req.api_endpoint,
                api_key=req.api_key,
            )
        data_source = get_data_source_for_provider(req.provider_name)
        if work.get("candidate_options"):
            data_source = "user_provided"
        result = plan_trip_data(data=work, topk=req.topk, input_name="fastapi_payload", data_source=data_source)
        if req.response_format == ResponseFormat.MARKDOWN:
            return PlainTextResponse(content=to_markdown(result), media_type="text/markdown")
        return result
    except ProviderError as err:
        raise HTTPException(status_code=502, detail=err.user_message) from err
    except ValueError as err:
        logger.warning("Invalid input: %s", str(err)[:200])
        raise HTTPException(status_code=422, detail="Invalid input data") from err
    except Exception as err:
        logger.error("Planning failed: %s", type(err).__name__)
        raise HTTPException(status_code=400, detail="Request processing failed") from err


@app.post("/plan/quick")
def quick_plan(req: QuickPlanRequest):
    try:
        payload = build_quick_payload(
            origins=req.origins,
            destination=req.destination,
            date=req.date,
            departure_time_range=req.departure_time_range,
            max_transfers=req.max_transfers,
            arrival_sync_minutes=req.arrival_sync_minutes,
            weight_profile=req.weight_profile,
        )
        city_warnings = []
        for t in payload.get("travelers", []):
            _, warns = resolve_city_with_validation(t["origin"])
            city_warnings.extend(warns)
        _, dest_warns = resolve_city_with_validation(req.destination)
        city_warnings.extend(dest_warns)

        payload["candidate_options"] = resolve_provider(
            provider_name=req.data_source,
            cache=cache,
            payload=payload,
        )
        result = plan_trip_data(data=payload, topk=req.topk, input_name="quick_plan", data_source=req.data_source)
        if city_warnings and result.get("route_analysis"):
            existing_warnings = result["route_analysis"].get("warnings", [])
            result["route_analysis"]["warnings"] = existing_warnings + city_warnings
        if req.response_format == ResponseFormat.MARKDOWN:
            return PlainTextResponse(content=to_markdown(result), media_type="text/markdown")
        return result
    except ProviderError as err:
        raise HTTPException(status_code=502, detail=err.user_message) from err
    except ValueError as err:
        logger.warning("Invalid input: %s", str(err)[:200])
        raise HTTPException(status_code=422, detail="Invalid input data") from err
    except Exception as err:
        logger.error("Quick planning failed: %s", type(err).__name__)
        raise HTTPException(status_code=400, detail="Request processing failed") from err


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("service_api:app", host="0.0.0.0", port=8000, reload=False)
