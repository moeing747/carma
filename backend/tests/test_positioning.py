"""The pure position-derivation reference: interpolation rule and geometry.

The synthetic schedule used throughout (seconds, single service day):

  stop  seq  lon (lat 52.5)  arrival  departure   shape fraction
  A     1    13.00           1000     1000        0.0
  B     2    13.02           1100     1160        0.2   (60s dwell)
  C     3    13.06           1300     1300        0.6
  D     4    13.10           1500     1500        1.0

The shape is a straight west->east line, so expected positions are exact.
"""

import pytest

from carma.domain.geometry import (
    bearing_at_fraction,
    initial_bearing,
    locate_fraction,
    point_at_fraction,
)
from carma.domain.models import Coordinate, ScheduledStop, StopTimeEvent
from carma.domain.positioning import (
    TripProgress,
    derive_position,
    effective_stop_times,
    progress_at,
)


def _stop(
    seq: int,
    lon: float,
    arrival: int | None,
    departure: int | None,
    lat: float = 52.5,
) -> ScheduledStop:
    return ScheduledStop(
        stop_id=f"S{seq}",
        stop_name=f"Stop {seq}",
        stop_sequence=seq,
        arrival_seconds=arrival,
        departure_seconds=departure,
        coordinate=Coordinate(lat=lat, lon=lon),
    )


def _event(
    seq: int, arrival: int | None = None, departure: int | None = None
) -> StopTimeEvent:
    return StopTimeEvent(
        stop_id=f"S{seq}",
        stop_sequence=seq,
        arrival_delay_seconds=arrival,
        departure_delay_seconds=departure,
    )


SHAPE = (Coordinate(lat=52.5, lon=13.0), Coordinate(lat=52.5, lon=13.1))
STOPS = (
    _stop(1, 13.00, 1000, 1000),
    _stop(2, 13.02, 1100, 1160),
    _stop(3, 13.06, 1300, 1300),
    _stop(4, 13.10, 1500, 1500),
)


# --- the interpolation rule (progress_at) ---


def test_parked_at_first_stop_before_first_departure() -> None:
    assert progress_at(STOPS, (), 500) == TripProgress(0, 0, 0.0, 0)


def test_travelling_midway_between_stops() -> None:
    progress = progress_at(STOPS, (), 1050)
    assert progress == TripProgress(0, 1, 0.5, 0)


def test_dwell_is_pinned_between_arrival_and_departure() -> None:
    for t in (1100, 1120, 1160):  # arrival edge, middle, departure edge
        progress = progress_at(STOPS, (), t)
        assert progress is not None
        assert progress.pinned and progress.from_index == 1, t


def test_trip_over_after_last_arrival() -> None:
    assert progress_at(STOPS, (), 1500) == TripProgress(3, 3, 0.0, 0)
    assert progress_at(STOPS, (), 1501) is None


def test_delay_applies_from_its_stop_onwards_not_before() -> None:
    events = (_event(2, arrival=120, departure=180),)
    # A (before the first event) is unadjusted: still parked until 1000.
    assert progress_at(STOPS, events, 999) == TripProgress(0, 0, 0.0, 0)
    # Travelling A->B stretches toward B's delayed arrival (1220).
    progress = progress_at(STOPS, events, 1050)
    assert progress is not None
    assert (progress.from_index, progress.to_index) == (0, 1)
    assert progress.fraction == pytest.approx(50 / 220)
    assert progress.delay_seconds == 120  # the target arrival's delay
    # Dwelling at B reports the departure delay.
    dwell = progress_at(STOPS, events, 1250)
    assert dwell is not None
    assert dwell.pinned and dwell.from_index == 1
    assert dwell.delay_seconds == 180


def test_departure_delay_propagates_over_stops_without_events() -> None:
    events = (_event(2, arrival=120, departure=180), _event(4, arrival=60))
    timed = effective_stop_times(STOPS, events)
    # C has no event: B's departure delay propagates (1300 + 180).
    assert timed[2].arrival_seconds == 1480
    # D has its own event again: its own arrival delay wins (1500 + 60).
    assert timed[3].arrival_seconds == 1560
    travelling = progress_at(STOPS, events, 1500)
    assert travelling is not None
    assert (travelling.from_index, travelling.to_index) == (2, 3)
    assert travelling.delay_seconds == 60


def test_trip_still_positioned_after_scheduled_end_when_delayed() -> None:
    events = (_event(2, arrival=120, departure=180),)
    late = progress_at(STOPS, events, 1600)  # past 1500, before 1680
    assert late is not None
    assert (late.from_index, late.to_index) == (2, 3)
    assert progress_at(STOPS, events, 1681) is None


def test_non_monotonic_delays_are_clamped() -> None:
    # +300 at B, back to 0 at C: C's raw times (1300) fall before B's
    # effective departure (1460) and must clamp up to it.
    events = (_event(2, arrival=300, departure=300), _event(3, arrival=0, departure=0))
    timed = effective_stop_times(STOPS, events)
    assert (timed[1].arrival_seconds, timed[1].departure_seconds) == (1400, 1460)
    assert (timed[2].arrival_seconds, timed[2].departure_seconds) == (1460, 1460)
    # The shared instant pins at the earlier stop (scan order).
    pinned = progress_at(STOPS, events, 1460)
    assert pinned is not None
    assert pinned.pinned and pinned.from_index == 1
    # Just after, the vehicle travels C->D against the clamped times.
    moving = progress_at(STOPS, events, 1470)
    assert moving is not None
    assert (moving.from_index, moving.to_index) == (2, 3)
    assert moving.fraction == pytest.approx(10 / 40)
    assert moving.delay_seconds == 0


def test_early_vehicle_with_negative_delay() -> None:
    events = (_event(1, departure=-120),)
    # 20% into the shortened A->B window (880 -> 980).
    early = progress_at(STOPS, events, 900)
    assert early is not None
    assert (early.from_index, early.to_index) == (0, 1)
    assert early.fraction == pytest.approx(0.2)
    assert early.delay_seconds == -120
    # At the scheduled A->B time the vehicle is already dwelling at B.
    ahead = progress_at(STOPS, events, 1000)
    assert ahead is not None
    assert ahead.pinned and ahead.from_index == 1
    assert ahead.delay_seconds == -120


def test_midnight_crossing_seconds_work_unchanged() -> None:
    stops = (
        _stop(1, 13.00, 86_000, 86_000),
        _stop(2, 13.10, 87_200, 87_200),  # 00:13:20 next calendar day
    )
    progress = progress_at(stops, (), 86_600)
    assert progress == TripProgress(0, 1, 0.5, 0)


def test_missing_arrival_or_departure_falls_back_to_the_other() -> None:
    stops = (
        _stop(1, 13.00, None, 1000),
        _stop(2, 13.02, 1100, None),
        _stop(3, 13.06, None, None),  # no schedule information: ignored
        _stop(4, 13.10, 1500, 1500),
    )
    timed = effective_stop_times(stops, ())
    assert [entry.stop.stop_sequence for entry in timed] == [1, 2, 4]
    assert (timed[0].arrival_seconds, timed[0].departure_seconds) == (1000, 1000)
    assert (timed[1].arrival_seconds, timed[1].departure_seconds) == (1100, 1100)


def test_event_between_stop_sequences_still_propagates() -> None:
    # An event for a sequence that matches no stop applies to later stops.
    events = (_event(2, departure=60),)
    stops = (_stop(1, 13.00, 1000, 1000), _stop(3, 13.06, 1300, 1300))
    timed = effective_stop_times(stops, events)
    assert timed[0].arrival_seconds == 1000
    assert timed[1].arrival_seconds == 1360


def test_empty_schedule_has_no_progress() -> None:
    assert progress_at((), (), 1000) is None


# --- projection onto geometry (derive_position) ---


def test_position_on_straight_shape() -> None:
    position = derive_position(STOPS, (), SHAPE, 1050)
    assert position is not None
    assert position.coordinate.lat == pytest.approx(52.5)
    assert position.coordinate.lon == pytest.approx(13.01)  # halfway A->B
    assert position.bearing == pytest.approx(90.0, abs=0.5)  # due east
    assert position.delay_seconds == 0


def test_dwell_position_is_the_stop_projected_onto_the_shape() -> None:
    # The stop sits slightly off the line; the position snaps onto it.
    stops = (STOPS[0], _stop(2, 13.02, 1100, 1160, lat=52.501), *STOPS[2:])
    position = derive_position(stops, (), SHAPE, 1120)
    assert position is not None
    assert position.coordinate.lat == pytest.approx(52.5)
    assert position.coordinate.lon == pytest.approx(13.02)
    assert position.bearing == pytest.approx(90.0, abs=0.5)


def test_position_on_curved_shape_follows_the_correct_leg() -> None:
    shape = (
        Coordinate(lat=52.5, lon=13.0),
        Coordinate(lat=52.5, lon=13.05),
        Coordinate(lat=52.55, lon=13.05),
    )
    stops = (
        _stop(1, 13.0, 1000, 1000),
        _stop(2, 13.05, 1100, 1100),  # the corner
        _stop(3, 13.05, 1300, 1300, lat=52.55),
    )
    east = derive_position(stops, (), shape, 1050)
    assert east is not None
    assert east.coordinate.lat == pytest.approx(52.5)
    assert east.bearing == pytest.approx(90.0, abs=0.5)
    north = derive_position(stops, (), shape, 1200)
    assert north is not None
    assert north.coordinate.lon == pytest.approx(13.05)
    assert 52.5 < north.coordinate.lat < 52.55
    assert north.bearing == pytest.approx(0.0, abs=0.5) or north.bearing > 359.5


def test_shapeless_trip_interpolates_between_stops() -> None:
    travelling = derive_position(STOPS, (), None, 1050)
    assert travelling is not None
    assert travelling.coordinate.lon == pytest.approx(13.01)
    assert travelling.bearing == pytest.approx(90.0, abs=0.5)
    pinned = derive_position(STOPS, (), None, 1120)
    assert pinned is not None
    assert pinned.coordinate == STOPS[1].coordinate
    assert pinned.bearing is None  # standing still, no line to point along


def test_no_position_after_the_trip_ends() -> None:
    assert derive_position(STOPS, (), SHAPE, 2000) is None


def test_delayed_position_trails_the_schedule_only_position() -> None:
    events = (_event(2, arrival=240, departure=240),)
    on_time = derive_position(STOPS, (), SHAPE, 1080)
    delayed = derive_position(STOPS, events, SHAPE, 1080)
    assert on_time is not None and delayed is not None
    assert delayed.coordinate.lon < on_time.coordinate.lon
    assert delayed.delay_seconds == 240


# --- geometry helpers ---


def test_locate_fraction_on_and_beyond_the_line() -> None:
    assert locate_fraction(SHAPE, Coordinate(lat=52.5, lon=13.05)) == pytest.approx(0.5)
    assert locate_fraction(SHAPE, Coordinate(lat=52.51, lon=13.05)) == pytest.approx(0.5)
    assert locate_fraction(SHAPE, Coordinate(lat=52.5, lon=12.9)) == 0.0
    assert locate_fraction(SHAPE, Coordinate(lat=52.5, lon=13.2)) == 1.0


def test_point_at_fraction_endpoints_and_clamping() -> None:
    assert point_at_fraction(SHAPE, 0.0) == SHAPE[0]
    assert point_at_fraction(SHAPE, 1.0) == SHAPE[-1]
    assert point_at_fraction(SHAPE, -0.5) == SHAPE[0]
    assert point_at_fraction(SHAPE, 1.5) == SHAPE[-1]
    mid = point_at_fraction(SHAPE, 0.5)
    assert mid.lon == pytest.approx(13.05)


def test_initial_bearing_cardinal_directions() -> None:
    a = Coordinate(lat=52.5, lon=13.0)
    assert initial_bearing(a, Coordinate(lat=52.5, lon=13.1)) == pytest.approx(90.0, abs=0.1)
    assert initial_bearing(a, Coordinate(lat=52.6, lon=13.0)) == pytest.approx(0.0, abs=0.1)
    assert initial_bearing(a, Coordinate(lat=52.4, lon=13.0)) == pytest.approx(180.0, abs=0.1)
    assert initial_bearing(a, a) is None


def test_bearing_at_the_end_of_the_line_looks_backwards_along_it() -> None:
    assert bearing_at_fraction(SHAPE, 1.0) == pytest.approx(90.0, abs=0.5)


def test_bearing_on_a_degenerate_line_is_none() -> None:
    point = Coordinate(lat=52.5, lon=13.0)
    assert bearing_at_fraction((point, point), 0.5) is None
