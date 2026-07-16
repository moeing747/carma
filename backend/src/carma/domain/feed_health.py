"""Snapshot ordering and feed-freshness rules.

The GTFS-RT feed is republished every ~30 seconds and consecutive snapshots
overlap: most trips appear in both. Two pure rules govern how the pipeline
handles that:

- latest-wins: a stored delay is replaced only by a strictly newer snapshot,
  so replays and out-of-order delivery never regress state;
- freshness: the pipeline is healthy while the newest ingested snapshot is at
  most FRESHNESS_WINDOW old.
"""

from datetime import datetime, timedelta

FRESHNESS_WINDOW = timedelta(seconds=120)


def snapshot_supersedes(incoming: datetime, existing: datetime | None) -> bool:
    """True when the incoming snapshot timestamp must replace the stored one.

    Strictly newer wins; an equal timestamp is the same snapshot redelivered
    (at-least-once consumption) and must not touch the row.
    """
    return existing is None or incoming > existing


def feed_age_seconds(last_snapshot_at: datetime, now: datetime) -> float:
    return (now - last_snapshot_at).total_seconds()


def is_feed_fresh(
    last_snapshot_at: datetime | None,
    now: datetime,
    window: timedelta = FRESHNESS_WINDOW,
) -> bool:
    """A feed with no snapshot at all is stale by definition."""
    return last_snapshot_at is not None and now - last_snapshot_at <= window
