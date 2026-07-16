from datetime import UTC, datetime, timedelta

from carma.domain.feed_health import (
    FRESHNESS_WINDOW,
    feed_age_seconds,
    is_feed_fresh,
    snapshot_supersedes,
)

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def test_first_snapshot_always_supersedes() -> None:
    assert snapshot_supersedes(NOW, existing=None)


def test_newer_snapshot_supersedes_older() -> None:
    assert snapshot_supersedes(NOW, existing=NOW - timedelta(seconds=30))


def test_equal_snapshot_is_a_redelivery_and_does_not_supersede() -> None:
    assert not snapshot_supersedes(NOW, existing=NOW)


def test_older_snapshot_never_regresses() -> None:
    assert not snapshot_supersedes(NOW - timedelta(seconds=30), existing=NOW)


def test_feed_with_recent_snapshot_is_fresh() -> None:
    assert is_feed_fresh(NOW - timedelta(seconds=119), now=NOW)


def test_freshness_window_boundary_is_inclusive() -> None:
    assert is_feed_fresh(NOW - FRESHNESS_WINDOW, now=NOW)


def test_feed_past_the_window_is_stale() -> None:
    assert not is_feed_fresh(NOW - FRESHNESS_WINDOW - timedelta(seconds=1), now=NOW)


def test_feed_without_any_snapshot_is_stale() -> None:
    assert not is_feed_fresh(None, now=NOW)


def test_feed_age_in_seconds() -> None:
    assert feed_age_seconds(NOW - timedelta(seconds=45), now=NOW) == 45.0
