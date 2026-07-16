from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from carma.domain.models import (
    BoundingBox,
    Coordinate,
    FeedStatus,
    ScheduledStop,
    TripDelay,
    TripId,
    VehiclePosition,
)


class FeedSource(Protocol):
    def fetch(self) -> bytes | None:
        """The current feed payload, or None when unchanged since the last
        fetch (e.g. an HTTP 304 on a conditional request)."""
        ...


class TripUpdatePublisher(Protocol):
    def publish(self, delay: TripDelay) -> None: ...


class TripDelayRepository(Protocol):
    def save(self, delay: TripDelay) -> None:
        """Latest-wins upsert: an older-or-equal snapshot must not regress
        the stored row (see carma.domain.feed_health.snapshot_supersedes)."""
        ...

    def latest_for_trip(self, trip_id: TripId) -> TripDelay | None: ...


class FeedStatusRepository(Protocol):
    def record_snapshot(self, snapshot_at: datetime, entity_count: int) -> None:
        """Advance the single feed-status row; ``last_snapshot_at`` must never
        move backwards so replays cannot fake freshness."""
        ...

    def latest(self) -> FeedStatus | None: ...


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


class PositionRecomputeEngine(Protocol):
    def recompute(self, trip_ids: Collection[TripId], at: datetime) -> int:
        """Replace the stored positions with ones derived for these trips.

        One set-based pass at the given instant: trips that yield a position
        are upserted, previously stored trips that no longer do are removed.
        Returns the number of positions written. Must implement the
        semantics documented in carma.domain.positioning.
        """
        ...


class VehiclePositionReader(Protocol):
    def positions(self, bbox: BoundingBox | None, limit: int) -> tuple[VehiclePosition, ...]:
        """Current positions, optionally restricted to a bounding box."""
        ...

    def positions_since(self, cursor: datetime | None, limit: int) -> tuple[VehiclePosition, ...]:
        """Positions computed strictly after ``cursor`` (all when None),
        ordered by computed_at so callers can advance a delta cursor."""
        ...


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
