"""Pure headway math: axis placement, gaps, spread, plan assembly."""

from carma.domain.headway import (
    HoldRecommendation,
    LineVehicle,
    build_plan,
    gaps,
    gaps_after_holds,
    max_abs_deviation,
    order_leader_first,
    pattern_position,
    stddev,
)
from carma.domain.models import Coordinate, ScheduledStop, TripId


def _vehicle(trip_id: str, position: float, delay: int = 0) -> LineVehicle:
    return LineVehicle(
        trip_id=TripId(trip_id),
        position_seconds=position,
        delay_seconds=delay,
        next_stop_id=f"stop-{trip_id}",
        next_stop_name=f"Stop {trip_id}",
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


# 08:00 -> 08:10/08:11 -> 08:20; pattern span 28800..30000.
SCHEDULE = (
    _stop(1, None, 28800),
    _stop(2, 29400, 29460),
    _stop(3, 30000, None),
)


def test_order_leader_first_sorts_by_position_then_id() -> None:
    ordered = order_leader_first(
        [_vehicle("b", 100.0), _vehicle("c", 900.0), _vehicle("a", 100.0)]
    )
    assert [vehicle.trip_id.value for vehicle in ordered] == ["c", "a", "b"]


def test_gaps_are_consecutive_headways() -> None:
    assert gaps([900.0, 600.0, 100.0]) == (300.0, 500.0)
    assert gaps([42.0]) == ()


def test_holds_move_vehicles_back_on_the_axis() -> None:
    # Holding the middle vehicle grows the gap ahead of it, shrinks the one behind.
    assert gaps_after_holds([900.0, 600.0, 100.0], [0, 100, 0]) == (400.0, 400.0)


def test_stddev_and_max_abs_deviation() -> None:
    assert stddev([500.0, 500.0]) == 0.0
    assert stddev([100.0, 900.0]) == 400.0
    assert stddev([42.0]) == 0.0
    assert max_abs_deviation([100.0, 900.0]) == 400.0
    assert max_abs_deviation([]) == 0.0


def test_build_plan_reports_per_vehicle_headways_and_summary() -> None:
    vehicles = (_vehicle("lead", 1000.0), _vehicle("mid", 900.0), _vehicle("tail", 0.0))
    plan = build_plan(vehicles, [0, 300, 0])

    assert [rec.hold_seconds for rec in plan.recommendations] == [0, 300, 0]
    leader, middle, tail = plan.recommendations
    assert leader == HoldRecommendation(
        trip_id=TripId("lead"),
        hold_seconds=0,
        next_stop_id="stop-lead",
        next_stop_name="Stop lead",
        headway_before_seconds=None,
        headway_after_seconds=None,
    )
    assert (middle.headway_before_seconds, middle.headway_after_seconds) == (100.0, 400.0)
    assert (tail.headway_before_seconds, tail.headway_after_seconds) == (900.0, 600.0)
    assert plan.summary.vehicle_count == 3
    assert plan.summary.headway_stddev_before_seconds == 400.0
    assert plan.summary.headway_stddev_after_seconds == 100.0


def test_build_plan_collapses_spread_worsening_holds_to_zero() -> None:
    # An already even line: any hold pattern that increases the stddev must
    # come back as the do-nothing plan.
    vehicles = (_vehicle("a", 1000.0), _vehicle("b", 500.0), _vehicle("c", 0.0))
    plan = build_plan(vehicles, [0, 300, 0])

    assert [rec.hold_seconds for rec in plan.recommendations] == [0, 0, 0]
    assert plan.summary.headway_stddev_after_seconds == 0.0


def test_pattern_position_mid_trip_with_delay() -> None:
    # 08:15 wall, 2 min late: effectively at schedule instant 08:13.
    located = pattern_position(SCHEDULE, delay_seconds=120, seconds_of_day=29700)
    assert located is not None
    position, next_stop = located
    assert position == 780.0  # 29580 - 28800
    assert next_stop.stop_id == "S3"  # S2 (29400) already effectively passed


def test_pattern_position_before_departure_clamps_to_start() -> None:
    located = pattern_position(SCHEDULE, delay_seconds=0, seconds_of_day=28500)
    assert located is not None
    position, next_stop = located
    assert position == 0.0
    assert next_stop.stop_id == "S1"


def test_pattern_position_past_the_end_clamps_to_span() -> None:
    located = pattern_position(SCHEDULE, delay_seconds=0, seconds_of_day=31200)
    assert located is not None
    position, next_stop = located
    assert position == 1200.0
    assert next_stop.stop_id == "S3"


def test_pattern_position_crosses_midnight_via_service_day_candidate() -> None:
    # 23:50 -> 25:10 GTFS times; at wall 00:10 the comparable instant is
    # 00:10 + 24h = 87000, inside the span.
    late = (_stop(1, None, 85800), _stop(2, 90600, None))
    located = pattern_position(late, delay_seconds=0, seconds_of_day=600)
    assert located is not None
    position, next_stop = located
    assert position == 1200.0
    assert next_stop.stop_id == "S2"


def test_pattern_position_without_timed_stops_is_none() -> None:
    untimed = (_stop(1, None, None),)
    assert pattern_position(untimed, delay_seconds=0, seconds_of_day=29700) is None
    assert pattern_position((), delay_seconds=0, seconds_of_day=29700) is None
