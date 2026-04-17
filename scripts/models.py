from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from station_repository import (
    get_all_cities,
    get_all_known_stations,
    get_cn_name_map,
    get_cross_station_transfer_minutes as _repo_cross_station_time,
    get_primary_station as _repo_primary_station,
    get_stations_for_city as _repo_stations_for_city,
    is_known_station as _repo_is_known_station,
    resolve_city_name as _repo_resolve_city_name,
    validate_city_has_hsr as _repo_validate_city,
    validate_station_for_city as _repo_validate_station,
)


DT_FMT = "%Y-%m-%d %H:%M"

MIN_TRANSFER_MINUTES = 15
MAX_TRANSFER_MINUTES = 180
MAX_COMBO_LIMIT = 50000
MAX_PLAN_TIME_SECONDS = 15.0


class StrategyType(str, Enum):
    SAME_TRAIN = "same-train"
    PARTIAL_SAME_TRAIN = "partial-same-train"
    TRANSFER_MERGE = "transfer-merge"
    SYNCHRONIZED_ARRIVAL = "synchronized-arrival"
    BEST_COMPROMISE = "best-compromise"


class ResponseFormat(str, Enum):
    JSON = "json"
    MARKDOWN = "markdown"


class WeightProfile(str, Enum):
    BALANCED = "balanced"
    SPEED_FIRST = "speed-first"
    COMFORT_FIRST = "comfort-first"
    BUDGET_FIRST = "budget-first"
    SYNC_FIRST = "sync-first"


WEIGHT_PROFILES: Dict[WeightProfile, Dict[str, float]] = {
    WeightProfile.BALANCED: {
        "travel_time": 0.25,
        "price": 0.15,
        "transfer_penalty": 0.20,
        "arrival_sync": 0.15,
        "comfort_penalty": 0.10,
        "priority_satisfaction": 0.05,
        "same_train_bonus": 0.10,
    },
    WeightProfile.SPEED_FIRST: {
        "travel_time": 0.40,
        "price": 0.05,
        "transfer_penalty": 0.15,
        "arrival_sync": 0.15,
        "comfort_penalty": 0.05,
        "priority_satisfaction": 0.05,
        "same_train_bonus": 0.15,
    },
    WeightProfile.COMFORT_FIRST: {
        "travel_time": 0.10,
        "price": 0.10,
        "transfer_penalty": 0.30,
        "arrival_sync": 0.10,
        "comfort_penalty": 0.25,
        "priority_satisfaction": 0.05,
        "same_train_bonus": 0.10,
    },
    WeightProfile.BUDGET_FIRST: {
        "travel_time": 0.10,
        "price": 0.40,
        "transfer_penalty": 0.15,
        "arrival_sync": 0.10,
        "comfort_penalty": 0.05,
        "priority_satisfaction": 0.05,
        "same_train_bonus": 0.15,
    },
    WeightProfile.SYNC_FIRST: {
        "travel_time": 0.15,
        "price": 0.10,
        "transfer_penalty": 0.10,
        "arrival_sync": 0.35,
        "comfort_penalty": 0.05,
        "priority_satisfaction": 0.05,
        "same_train_bonus": 0.20,
    },
}


def _build_station_aliases() -> Dict[str, List[str]]:
    result = {}
    for city_key, city_data in get_all_cities().items():
        result[city_key] = city_data.get("stations", [city_key])
    return result


def _build_primary_stations() -> Dict[str, str]:
    result = {}
    for city_key, city_data in get_all_cities().items():
        primary = city_data.get("primary_hsr_station", "")
        if primary:
            result[city_key] = primary
    return result


def _build_cross_station_times() -> Dict[Tuple[str, str], int]:
    from station_repository import _load_station_data
    data = _load_station_data()
    raw = data.get("cross_station_transfer_times", {})
    result = {}
    for key, minutes in raw.items():
        parts = key.split("|")
        if len(parts) == 2:
            result[(parts[0], parts[1])] = minutes
            result[(parts[1], parts[0])] = minutes
    return result


STATION_ALIASES: Dict[str, List[str]] = _build_station_aliases()
HSR_PRIMARY_STATION: Dict[str, str] = _build_primary_stations()
CROSS_STATION_TRANSFER_TIMES: Dict[Tuple[str, str], int] = _build_cross_station_times()

ALL_KNOWN_STATIONS: set = get_all_known_stations()


def city_to_stations(city: str) -> List[str]:
    return _repo_stations_for_city(city)


def station_belongs_to_city(station: str, city: str) -> bool:
    resolved = _repo_resolve_city_name(city)
    stations = _repo_stations_for_city(resolved)
    return station in stations or resolved.split("_")[0] in station


def is_known_station(station_name: str) -> bool:
    return _repo_is_known_station(station_name)


def get_cross_station_transfer_minutes(station_a: str, station_b: str) -> Optional[int]:
    return _repo_cross_station_time(station_a, station_b)


def validate_station_for_city(station_name: str, city_name: str) -> Tuple[bool, str]:
    return _repo_validate_station(station_name, city_name)


class Leg(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    train_no: str = Field(..., description="Train number e.g. G80, D102", min_length=1)
    dep_station: str = Field(..., description="Departure station name", min_length=1)
    arr_station: str = Field(..., description="Arrival station name", min_length=1)
    dep_time: str = Field(..., description="Departure time in YYYY-MM-DD HH:MM format")
    arr_time: str = Field(..., description="Arrival time in YYYY-MM-DD HH:MM format")

    @field_validator("dep_time", "arr_time")
    @classmethod
    def validate_datetime(cls, v: str) -> str:
        datetime.strptime(v, DT_FMT)
        return v

    @model_validator(mode="after")
    def validate_time_order(self) -> "Leg":
        dep = datetime.strptime(self.dep_time, DT_FMT)
        arr = datetime.strptime(self.arr_time, DT_FMT)
        travel_time = arr - dep
        if travel_time.total_seconds() <= 0:
            travel_time = arr + timedelta(days=1) - dep
            if travel_time > timedelta(hours=24):
                raise ValueError(
                    f"Cross-day travel time {travel_time.total_seconds()/3600:.1f}h exceeds 24h limit "
                    f"for leg {self.train_no}: {self.dep_time} -> {self.arr_time}"
                )
            if dep.hour < 6 and dep.hour >= 0:
                pass
            elif dep.hour < 18:
                is_d_series = self.train_no.upper().startswith("D")
                if not is_d_series:
                    raise ValueError(
                        f"Cross-day G-series train {self.train_no} departs at {self.dep_time[11:]} (before 18:00), "
                        f"which is unrealistic for overnight HSR. arr_time: {self.arr_time}"
                    )
        else:
            if travel_time > timedelta(hours=12):
                is_d_series = self.train_no.upper().startswith("D")
                if is_d_series and travel_time <= timedelta(hours=24):
                    pass
                else:
                    raise ValueError(
                        f"Travel time exceeds 12h for leg {self.train_no}: "
                        f"{self.dep_time} -> {self.arr_time}"
                    )
        return self


class CandidateOption(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    strategy: StrategyType = Field(..., description="Routing strategy for this option")
    arrival_time: str = Field(..., description="Final arrival time in YYYY-MM-DD HH:MM format")
    total_minutes: int = Field(..., description="Total travel duration in minutes", gt=0)
    total_price: float = Field(..., description="Total ticket price in CNY", ge=0)
    total_transfers: int = Field(..., description="Number of transfers", ge=0)
    train_type: str = Field(..., description="Train type e.g. HSR, D-series", min_length=1)
    seat_type: str = Field(..., description="Seat type e.g. second-class, first-class, standing", min_length=1)
    merge_hub: str = Field(default="", description="Transfer hub station name, empty if no transfer")
    shared_train_ratio: float = Field(
        default=0.0,
        description="Ratio of shared train segments across travelers, 0.0 to 1.0",
        ge=0.0,
        le=1.0,
    )
    is_estimated: bool = Field(
        default=False,
        description="Whether this option uses distance-based estimation instead of real schedule data",
    )
    data_source: str = Field(
        default="",
        description="Data source identifier: 12306-direct, 12306-mcp, distance-estimation, etc.",
    )
    legs: List[Leg] = Field(..., description="List of train legs in this option", min_length=1)

    @field_validator("arrival_time")
    @classmethod
    def validate_arrival_time(cls, v: str) -> str:
        datetime.strptime(v, DT_FMT)
        return v


class TravelerPreferences(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    max_transfers: Optional[int] = Field(default=None, description="Max transfers for this traveler", ge=0)
    avoid_transfer: Optional[bool] = Field(default=None, description="If true, reject options with any transfer")


class Traveler(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(..., description="Traveler identifier", min_length=1, max_length=50)
    origin: str = Field(..., description="Departure city name", min_length=1)
    earliest_departure: str = Field(..., description="Earliest acceptable departure time YYYY-MM-DD HH:MM")
    latest_departure: str = Field(..., description="Latest acceptable departure time YYYY-MM-DD HH:MM")
    priority_weight: float = Field(default=1.0, description="Priority weight for scoring, higher = more important", ge=0.0)
    preferences: TravelerPreferences = Field(default_factory=TravelerPreferences)

    @field_validator("earliest_departure", "latest_departure")
    @classmethod
    def validate_departure_time(cls, v: str) -> str:
        datetime.strptime(v, DT_FMT)
        return v

    @model_validator(mode="after")
    def validate_departure_window(self) -> "Traveler":
        start = datetime.strptime(self.earliest_departure, DT_FMT)
        end = datetime.strptime(self.latest_departure, DT_FMT)
        if end < start:
            raise ValueError(
                f"latest_departure {self.latest_departure} must be >= earliest_departure {self.earliest_departure}"
            )
        return self


class Constraints(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    max_transfers: int = Field(default=99, description="Maximum total transfers across all travelers", ge=0)
    max_arrival_time_diff_minutes: int = Field(
        ..., description="Maximum arrival time gap between travelers in minutes", ge=0
    )
    latest_arrival: Optional[str] = Field(
        default=None, description="Latest acceptable arrival time YYYY-MM-DD HH:MM"
    )
    must_train_types: List[str] = Field(
        default_factory=list, description="Allowed train types e.g. ['HSR']", max_length=10
    )
    accept_standing: bool = Field(default=True, description="Whether standing/no-seat tickets are acceptable")
    allowed_transfer_hubs: List[str] = Field(
        default_factory=list, description="Whitelist of allowed transfer hub stations"
    )

    @field_validator("latest_arrival")
    @classmethod
    def validate_latest_arrival(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            datetime.strptime(v, DT_FMT)
        return v


class Weights(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    travel_time: float = Field(..., description="Weight for total travel time penalty", gt=0)
    price: float = Field(..., description="Weight for total price penalty", gt=0)
    transfer_penalty: float = Field(..., description="Weight for transfer count penalty", gt=0)
    arrival_sync: float = Field(..., description="Weight for arrival time gap penalty", gt=0)
    comfort_penalty: float = Field(..., description="Weight for comfort penalty (standing + transfers)", gt=0)
    priority_satisfaction: float = Field(..., description="Weight for priority traveler satisfaction bonus", gt=0)
    same_train_bonus: float = Field(..., description="Weight for same-train shared ratio bonus", gt=0)


class TripPlannerInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    travelers: List[Traveler] = Field(..., description="List of travelers with departure info", min_length=1)
    destination: str = Field(..., description="Common destination city name", min_length=1)
    date: str = Field(..., description="Travel date in YYYY-MM-DD format", min_length=1)
    constraints: Constraints = Field(..., description="Hard constraints for itinerary filtering")
    weights: Weights = Field(..., description="Scoring weight profile")
    candidate_options: Dict[str, List[CandidateOption]] = Field(
        ..., description="Candidate options keyed by traveler name"
    )


class PlanOption(BaseModel):
    traveler: str
    strategy: str
    arrival_time: str
    total_minutes: int
    total_price: float
    total_transfers: int
    train_type: str
    seat_type: str
    merge_hub: str
    shared_train_ratio: float
    legs: List[Leg]


class RouteAnalysisIssue(BaseModel):
    severity: str = Field(description="Issue severity: warning or error")
    dimension: str = Field(description="What dimension the issue relates to")
    message: str = Field(description="Human-readable description of the issue")


class RouteAnalysisResult(BaseModel):
    is_valid: bool = True
    data_source: str = "mock"
    data_source_reliability: str = "low"
    issues: List[RouteAnalysisIssue] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class RankedPlan(BaseModel):
    score: float
    strategy_mix: List[str]
    is_best_compromise: bool = False
    arrival_spread_minutes: int
    total_minutes: int
    total_price: float
    total_transfers: int
    comfort_penalty: int
    reasoning: str = ""
    route_analysis: Optional[RouteAnalysisResult] = None
    options: List[PlanOption]


class RelaxationSuggestion(BaseModel):
    dimension: str
    current_value: Any
    suggested_value: Any
    description: str
    feasible_count_after: int = 0


class TripPlannerMeta(BaseModel):
    input_file: str = ""
    generated_at: str = ""
    planning_time_seconds: float = 0.0
    total_combinations: int = 0
    feasible_combinations: int = 0
    timed_out: bool = False
    constraints: Dict[str, Any] = Field(default_factory=dict)
    weights: Dict[str, float] = Field(default_factory=dict)
    data_source: str = "mock"
    data_source_reliability: str = "low"


class TripPlannerOutput(BaseModel):
    top_plans: List[RankedPlan] = Field(default_factory=list)
    total_feasible: int = Field(default=0, ge=0)
    message: str = ""
    relaxation_suggestions: List[RelaxationSuggestion] = Field(default_factory=list)
    route_analysis: Optional[RouteAnalysisResult] = None
    meta: Optional[TripPlannerMeta] = None
