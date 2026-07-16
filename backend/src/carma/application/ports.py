from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from carma.domain.models import Coordinate, ScheduledStop, TripDelay, TripId, VehiclePosition


class FeedSource(Protocol):
    def fetch(self) -> bytes: ...


class TripUpdatePublisher(Protocol):
    def publish(self, delay: TripDelay) -> None: ...


class TripDelayRepository(Protocol):
    def save(self, delay: TripDelay) -> None: ...

    def latest_for_trip(self, trip_id: TripId) -> TripDelay | None: ...


class TripScheduleRepository(Protocol):
    """Read access to the loaded static GTFS schedule."""

    def active_trip_ids(self, at: datetime) -> frozenset[TripId]:
        """Trips scheduled to be underway at the given instant.

        A timezone-aware instant is converted to the feed's agency timezone;
        a naive one is taken as feed-local wall time. Trips from the previous
        service day whose stop times run past midnight are included (see
        carma.domain.service_days for the convention).
        """
        ...

    def schedule_for_trip(self, trip_id: TripId) -> tuple[ScheduledStop, ...]:
        """Stop events for a trip, ordered by stop_sequence; empty if unknown."""
        ...

    def shape_for_trip(self, trip_id: TripId) -> tuple[Coordinate, ...] | None:
        """The trip's shape as ordered coordinates; None if it has no shape."""
        ...


class PositionProjector(Protocol):
    def project(self, delay: TripDelay) -> VehiclePosition | None: ...


@dataclass(frozen=True, slots=True)
class OptimizationRequest:
    route_ids: tuple[str, ...]
    horizon_minutes: int


@dataclass(frozen=True, slots=True)
class TripReassignment:
    trip_id: TripId
    from_vehicle_id: str
    to_vehicle_id: str


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    reassignments: tuple[TripReassignment, ...]
    objective_delay_seconds: int


class OptimizationEngine(Protocol):
    def solve(self, request: OptimizationRequest) -> OptimizationResult: ...
