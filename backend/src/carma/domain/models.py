from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class TripId:
    value: str


@dataclass(frozen=True, slots=True)
class StopTimeEvent:
    stop_id: str
    stop_sequence: int
    arrival_delay_seconds: int | None
    departure_delay_seconds: int | None


@dataclass(frozen=True, slots=True)
class TripDelay:
    trip_id: TripId
    route_id: str
    timestamp: datetime
    stop_time_events: tuple[StopTimeEvent, ...]


@dataclass(frozen=True, slots=True)
class FeedStatus:
    """Ingestion progress: when the newest applied snapshot was published
    upstream and how many trip updates the last applied batch carried."""

    last_snapshot_at: datetime
    last_entity_count: int


@dataclass(frozen=True, slots=True)
class Coordinate:
    lat: float
    lon: float


@dataclass(frozen=True, slots=True)
class ScheduledStop:
    # arrival/departure are seconds since service-day start and may exceed
    # 86400 on trips running past midnight (GTFS times like 25:10:00).
    stop_id: str
    stop_name: str
    stop_sequence: int
    arrival_seconds: int | None
    departure_seconds: int | None
    coordinate: Coordinate


@dataclass(frozen=True, slots=True)
class VehiclePosition:
    # The VBB feed carries no GPS: positions are always computed by projecting
    # schedule progress plus live delay onto the trip's shape geometry.
    trip_id: TripId
    route_id: str
    lat: float
    lon: float
    bearing: float | None
    computed_at: datetime
