from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from carma.application.ports import (
    FeedSource,
    FeedStatusRepository,
    OptimizationEngine,
    OptimizationRequest,
    PositionRecomputeEngine,
    TripDelayRepository,
    TripScheduleRepository,
    TripUpdatePublisher,
    VehiclePositionReader,
)
from carma.domain.errors import NotEnoughVehiclesError, UnknownLineError
from carma.domain.headway import (
    MIN_VEHICLES,
    HeadwayPlan,
    LineVehicle,
    order_leader_first,
    pattern_position,
)
from carma.domain.models import TripDelay, VehiclePosition


@dataclass(frozen=True, slots=True)
class IngestFeedSnapshot:
    source: FeedSource
    decode: Callable[[bytes], list[TripDelay]]
    publisher: TripUpdatePublisher

    def execute(self) -> int | None:
        """Fetch, decode, and publish one snapshot; the count published.

        None when the source reports the feed unchanged: nothing is
        published, which the poller logs distinctly from an empty snapshot.
        """
        payload = self.source.fetch()
        if payload is None:
            return None
        delays = self.decode(payload)
        for delay in delays:
            self.publisher.publish(delay)
        return len(delays)


@dataclass(frozen=True, slots=True)
class ApplyTripDelays:
    """Consumer side: persist a batch of TripDelays and advance feed status.

    save() is latest-wins, so applying overlapping or replayed batches is
    idempotent. The status snapshot timestamp is the newest feed timestamp in
    the batch (the repository guarantees it never regresses).
    """

    repository: TripDelayRepository
    feed_status: FeedStatusRepository

    def execute(self, delays: Sequence[TripDelay]) -> None:
        if not delays:
            return
        for delay in delays:
            self.repository.save(delay)
        self.feed_status.record_snapshot(
            snapshot_at=max(delay.timestamp for delay in delays),
            entity_count=len(delays),
        )


@dataclass(frozen=True, slots=True)
class RecomputeReport:
    active_trips: int
    positions_written: int


@dataclass(frozen=True, slots=True)
class RecomputePositions:
    """One projection tick: resolve the active trips, hand them to the
    set-based engine, report the counts the worker logs."""

    schedule: TripScheduleRepository
    engine: PositionRecomputeEngine

    def execute(self, at: datetime) -> RecomputeReport:
        trip_ids = self.schedule.active_trip_ids(at)
        written = self.engine.recompute(trip_ids, at)
        return RecomputeReport(active_trips=len(trip_ids), positions_written=written)


# All of VBB is well under this; it only bounds the reader scan.
_LINE_SCAN_LIMIT = 20_000


@dataclass(frozen=True, slots=True)
class LineOptimization:
    """An advisory headway plan for one line and direction."""

    route_short_name: str
    direction: str
    engine: str
    vehicles: tuple[LineVehicle, ...]
    plan: HeadwayPlan


@dataclass(frozen=True, slots=True)
class OptimizeLineHeadways:
    """Gather a line's live vehicles, locate them on the pattern-time axis,
    and hand them to the optimization engine behind the port.

    A line usually runs two directions at once; holds only make sense among
    vehicles chasing each other, so the busiest direction (largest headsign
    group, ties broken alphabetically) is optimized per request.
    """

    positions: VehiclePositionReader
    schedule: TripScheduleRepository
    engine: OptimizationEngine

    def execute(self, route_short_name: str, at: datetime) -> LineOptimization:
        """Plan for the line at a naive feed-local instant.

        Raises UnknownLineError when no live vehicle carries the line name,
        NotEnoughVehiclesError when its busiest direction is too thin.
        """
        rows = self.positions.positions(None, _LINE_SCAN_LIMIT)
        line_rows = [row for row in rows if row.route_short_name == route_short_name]
        if not line_rows:
            raise UnknownLineError(
                f"no live vehicles on line {route_short_name!r} — "
                "unknown line, or none of its trips are active right now"
            )
        direction, group = _busiest_direction(line_rows)
        seconds_of_day = at.hour * 3600 + at.minute * 60 + at.second
        vehicles = []
        for row in group:
            located = pattern_position(
                self.schedule.schedule_for_trip(row.trip_id), row.delay_seconds, seconds_of_day
            )
            if located is None:  # no usable schedule; cannot place it on the axis
                continue
            position_seconds, next_stop = located
            vehicles.append(
                LineVehicle(
                    trip_id=row.trip_id,
                    position_seconds=position_seconds,
                    delay_seconds=row.delay_seconds,
                    next_stop_id=next_stop.stop_id,
                    next_stop_name=next_stop.stop_name,
                )
            )
        if len(vehicles) < MIN_VEHICLES:
            raise NotEnoughVehiclesError(
                f"line {route_short_name!r} has only {len(vehicles)} schedulable "
                f"vehicle(s) towards {direction or '?'} right now; headway "
                f"re-spacing needs at least {MIN_VEHICLES}"
            )
        ordered = order_leader_first(vehicles)
        plan = self.engine.solve(
            OptimizationRequest(
                route_short_name=route_short_name, direction=direction, vehicles=ordered
            )
        )
        return LineOptimization(
            route_short_name=route_short_name,
            direction=direction,
            engine=self.engine.name,
            vehicles=ordered,
            plan=plan,
        )


def _busiest_direction(
    rows: Sequence[VehiclePosition],
) -> tuple[str, tuple[VehiclePosition, ...]]:
    groups: dict[str, list[VehiclePosition]] = {}
    for row in rows:
        groups.setdefault(row.headsign, []).append(row)
    direction = min(groups, key=lambda headsign: (-len(groups[headsign]), headsign))
    return direction, tuple(groups[direction])
