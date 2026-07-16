from datetime import UTC, datetime, timedelta

from carma.application.position_stream import advance_cursor
from carma.domain.models import TripId, VehiclePosition

BASE = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def _position(trip_id: str, computed_at: datetime) -> VehiclePosition:
    return VehiclePosition(
        trip_id=TripId(trip_id),
        route_id="R1",
        route_short_name="M1",
        lat=52.5,
        lon=13.4,
        bearing=90.0,
        delay_seconds=0,
        computed_at=computed_at,
    )


def test_empty_batch_keeps_the_cursor() -> None:
    assert advance_cursor(None, ()) is None
    assert advance_cursor(BASE, ()) == BASE


def test_first_batch_sets_the_cursor_to_the_newest_row() -> None:
    rows = (
        _position("a", BASE),
        _position("b", BASE + timedelta(seconds=5)),
        _position("c", BASE + timedelta(seconds=2)),
    )
    assert advance_cursor(None, rows) == BASE + timedelta(seconds=5)


def test_cursor_only_advances() -> None:
    newer = advance_cursor(BASE, (_position("a", BASE + timedelta(seconds=5)),))
    assert newer == BASE + timedelta(seconds=5)
    # A replayed batch of older rows must not rewind it.
    assert advance_cursor(newer, (_position("a", BASE),)) == newer
