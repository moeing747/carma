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
    # schedule progress plus live delay onto the trip's shape geometry
    # (carma.domain.positioning documents the semantics).
    trip_id: TripId
    route_id: str
    route_short_name: str
    lat: float
    lon: float
    bearing: float | None
    delay_seconds: int
    computed_at: datetime
    # Presentation metadata joined from the static schedule at read time;
    # defaulted so the projection engine never has to know about it.
    headsign: str = ""


@dataclass(frozen=True, slots=True)
class BoundingBox:
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    def __post_init__(self) -> None:
        if not (-180.0 <= self.min_lon <= self.max_lon <= 180.0):
            raise ValueError("longitudes must satisfy -180 <= min <= max <= 180")
        if not (-90.0 <= self.min_lat <= self.max_lat <= 90.0):
            raise ValueError("latitudes must satisfy -90 <= min <= max <= 90")
