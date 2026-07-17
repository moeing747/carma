"""OptimizeLineHeadways with a fake engine behind the port."""

from datetime import datetime

import pytest

from carma.application.ports import OptimizationRequest
from carma.application.position_stream import PositionCursor
from carma.application.use_cases import OptimizeLineHeadways
from carma.domain.errors import NotEnoughVehiclesError, UnknownLineError
from carma.domain.headway import HeadwayPlan, build_plan
from carma.domain.models import (
    BoundingBox,
    Coordinate,
    ScheduledStop,
    TripId,
    VehiclePosition,
)

AT = datetime(2026, 7, 14, 8, 15)  # naive feed-local, mid-pattern


class FakeEngine:
    """Test double of the production heuristic twin: zero-hold plans."""

    name = "fake"

    def __init__(self) -> None:
        self.requests: list[OptimizationRequest] = []

    def solve(self, request: OptimizationRequest) -> HeadwayPlan:
        self.requests.append(request)
        return build_plan(request.vehicles, [0] * len(request.vehicles))


class StubReader:
    def __init__(self, rows: tuple[VehiclePosition, ...]) -> None:
        self.rows = rows

    def positions(self, bbox: BoundingBox | None, limit: int) -> tuple[VehiclePosition, ...]:
        return self.rows[:limit]

    def positions_since(
        self, cursor: PositionCursor | None, limit: int
    ) -> tuple[VehiclePosition, ...]:
        return self.rows[:limit]

    def position_for_trip(self, trip_id: TripId) -> VehiclePosition | None:
        return None


class StubSchedule:
    def __init__(self, schedules: dict[str, tuple[ScheduledStop, ...]]) -> None:
        self.schedules = schedules

    def active_trip_ids(self, at: datetime) -> frozenset[TripId]:
        return frozenset()

    def schedule_for_trip(self, trip_id: TripId) -> tuple[ScheduledStop, ...]:
        return self.schedules.get(trip_id.value, ())

    def shape_for_trip(self, trip_id: TripId) -> tuple[Coordinate, ...] | None:
        return None


def _row(trip_id: str, line: str, headsign: str, delay: int) -> VehiclePosition:
    return VehiclePosition(
        trip_id=TripId(trip_id),
        route_id=f"route-{line}",
        route_short_name=line,
        lat=52.52,
        lon=13.41,
        bearing=None,
        delay_seconds=delay,
        computed_at=datetime(2026, 7, 14, 6, 15),
        headsign=headsign,
    )


def _stop(sequence: int, arrival: int | None, departure: int | None) -> ScheduledStop:
    return ScheduledStop(
        stop_id=f"S{sequence}",
        stop_name=f"Stop {sequence}",
        stop_sequence=sequence,
        arrival_seconds=arrival,
        departure_seconds=departure,
        coordinate=Coordinate(lat=52.5, lon=13.4),
    )


# One shared 08:00 -> 08:20 pattern; vehicles differ by delay only.
PATTERN: tuple[ScheduledStop, ...] = (
    _stop(1, None, 28800),
    _stop(2, 29400, 29460),
    _stop(3, 30000, None),
)

NORTH = (
    _row("A", "M1", "North", delay=0),  # effective 08:15 -> position 900
    _row("B", "M1", "North", delay=300),  # effective 08:10 -> position 600
    _row("C", "M1", "North", delay=600),  # effective 08:05 -> position 300
)
SOUTH = (_row("D", "M1", "South", delay=0), _row("E", "M1", "South", delay=0))
OTHER_LINE = (_row("F", "S1", "North", delay=0),)


def _use_case(
    rows: tuple[VehiclePosition, ...], engine: FakeEngine | None = None
) -> tuple[OptimizeLineHeadways, FakeEngine]:
    engine = engine if engine is not None else FakeEngine()
    schedules = {row.trip_id.value: PATTERN for row in rows}
    return (
        OptimizeLineHeadways(
            positions=StubReader(rows), schedule=StubSchedule(schedules), engine=engine
        ),
        engine,
    )


def test_optimizes_the_busiest_direction_of_the_requested_line() -> None:
    use_case, engine = _use_case(NORTH + SOUTH + OTHER_LINE)

    result = use_case.execute("M1", at=AT)

    assert result.route_short_name == "M1"
    assert result.direction == "North"  # 3 vehicles beat South's 2
    assert result.engine == "fake"
    request = engine.requests[0]
    assert request.direction == "North"
    # Leader-first on the pattern-time axis: least-delayed is furthest along.
    assert [vehicle.trip_id.value for vehicle in request.vehicles] == ["A", "B", "C"]
    assert [vehicle.position_seconds for vehicle in request.vehicles] == [900.0, 600.0, 300.0]
    # Next stop reflects the vehicle's own effective progress.
    assert [vehicle.next_stop_id for vehicle in request.vehicles] == ["S3", "S3", "S2"]
    assert result.vehicles == request.vehicles
    assert result.plan.summary.vehicle_count == 3


def test_unknown_line_raises() -> None:
    use_case, _ = _use_case(NORTH)

    with pytest.raises(UnknownLineError, match="'M99'"):
        use_case.execute("M99", at=AT)


def test_too_few_vehicles_in_the_busiest_direction_raises() -> None:
    use_case, _ = _use_case(SOUTH)

    with pytest.raises(NotEnoughVehiclesError, match="only 2"):
        use_case.execute("M1", at=AT)


def test_vehicles_without_a_schedule_cannot_be_placed_and_may_thin_the_line() -> None:
    rows = NORTH
    engine = FakeEngine()
    schedules = {"A": PATTERN, "B": PATTERN}  # C has no schedule rows
    use_case = OptimizeLineHeadways(
        positions=StubReader(rows), schedule=StubSchedule(schedules), engine=engine
    )

    with pytest.raises(NotEnoughVehiclesError, match="only 2 schedulable"):
        use_case.execute("M1", at=AT)
    assert engine.requests == []
