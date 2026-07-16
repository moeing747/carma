from dataclasses import dataclass
from typing import Protocol

from carma.domain.models import TripDelay, TripId, VehiclePosition


class FeedSource(Protocol):
    def fetch(self) -> bytes: ...


class TripUpdatePublisher(Protocol):
    def publish(self, delay: TripDelay) -> None: ...


class TripDelayRepository(Protocol):
    def save(self, delay: TripDelay) -> None: ...

    def latest_for_trip(self, trip_id: TripId) -> TripDelay | None: ...


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
