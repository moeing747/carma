from datetime import UTC, datetime, timedelta

from carma.application.position_stream import PositionCursor, advance_cursor
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
    cursor = PositionCursor(BASE, "a")
    assert advance_cursor(cursor, ()) == cursor


def test_first_batch_sets_the_cursor_to_the_newest_row() -> None:
    rows = (
        _position("a", BASE),
        _position("b", BASE + timedelta(seconds=5)),
        _position("c", BASE + timedelta(seconds=2)),
    )
    assert advance_cursor(None, rows) == PositionCursor(BASE + timedelta(seconds=5), "b")


def test_cursor_only_advances() -> None:
    cursor = PositionCursor(BASE, "a")
    newer = advance_cursor(cursor, (_position("a", BASE + timedelta(seconds=5)),))
    assert newer == PositionCursor(BASE + timedelta(seconds=5), "a")
    # A replayed batch of older rows must not rewind it.
    assert advance_cursor(newer, (_position("a", BASE),)) == newer


def test_same_timestamp_batches_advance_by_trip_id() -> None:
    """Keyset regression: a tick split by the read limit leaves rows at the
    cursor's timestamp; the trip id must carry the cursor through them."""
    first_page = advance_cursor(None, (_position("a", BASE), _position("b", BASE)))
    assert first_page == PositionCursor(BASE, "b")
    second_page = advance_cursor(first_page, (_position("c", BASE),))
    assert second_page == PositionCursor(BASE, "c")
    # Same timestamp but an earlier trip id must not rewind.
    assert advance_cursor(second_page, (_position("a", BASE),)) == second_page
