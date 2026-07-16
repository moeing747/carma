"""PostGIS-backed TripDelayRepository and FeedStatusRepository.

Callers own the connection; write methods open their own transactions, so
pass a connection with autocommit=True (psycopg then runs each
``transaction()`` block as a real, durable transaction).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from carma.domain.feed_health import snapshot_supersedes
from carma.domain.models import FeedStatus, StopTimeEvent, TripDelay, TripId


@dataclass(frozen=True, slots=True)
class PostgisTripDelayRepository:
    conn: psycopg.Connection[Any]

    def save(self, delay: TripDelay) -> None:
        """Latest-wins upsert keyed by trip_id.

        The decision lives in domain code (snapshot_supersedes); the row lock
        makes read-decide-write safe even if a second consumer ever runs.
        """
        with self.conn.transaction():
            row = self.conn.execute(
                "SELECT feed_timestamp FROM trip_delays WHERE trip_id = %s FOR UPDATE",
                (delay.trip_id.value,),
            ).fetchone()
            existing = row[0] if row is not None else None
            if not snapshot_supersedes(delay.timestamp, existing):
                return
            self.conn.execute(
                "INSERT INTO trip_delays"
                " (trip_id, route_id, feed_timestamp, received_at, stop_time_events)"
                " VALUES (%s, %s, %s, now(), %s)"
                " ON CONFLICT (trip_id) DO UPDATE SET"
                "  route_id = EXCLUDED.route_id,"
                "  feed_timestamp = EXCLUDED.feed_timestamp,"
                "  received_at = EXCLUDED.received_at,"
                "  stop_time_events = EXCLUDED.stop_time_events",
                (
                    delay.trip_id.value,
                    delay.route_id,
                    delay.timestamp,
                    Jsonb([_event_to_json(event) for event in delay.stop_time_events]),
                ),
            )

    def latest_for_trip(self, trip_id: TripId) -> TripDelay | None:
        row = self.conn.execute(
            "SELECT route_id, feed_timestamp, stop_time_events"
            " FROM trip_delays WHERE trip_id = %s",
            (trip_id.value,),
        ).fetchone()
        if row is None:
            return None
        route_id, feed_timestamp, events = row
        return TripDelay(
            trip_id=trip_id,
            route_id=route_id,
            timestamp=feed_timestamp,
            stop_time_events=tuple(_event_from_json(event) for event in events),
        )


@dataclass(frozen=True, slots=True)
class PostgisFeedStatusRepository:
    conn: psycopg.Connection[Any]

    def record_snapshot(self, snapshot_at: datetime, entity_count: int) -> None:
        with self.conn.transaction():
            self.conn.execute(
                "INSERT INTO feed_status (id, last_snapshot_at, last_entity_count, updated_at)"
                " VALUES (TRUE, %s, %s, now())"
                " ON CONFLICT (id) DO UPDATE SET"
                # GREATEST: replaying old data must not fake freshness.
                "  last_snapshot_at ="
                "   GREATEST(feed_status.last_snapshot_at, EXCLUDED.last_snapshot_at),"
                "  last_entity_count = EXCLUDED.last_entity_count,"
                "  updated_at = now()",
                (snapshot_at, entity_count),
            )

    def latest(self) -> FeedStatus | None:
        row = self.conn.execute(
            "SELECT last_snapshot_at, last_entity_count FROM feed_status"
        ).fetchone()
        if row is None:
            return None
        return FeedStatus(last_snapshot_at=row[0], last_entity_count=row[1])


def _event_to_json(event: StopTimeEvent) -> dict[str, object]:
    return {
        "stop_id": event.stop_id,
        "stop_sequence": event.stop_sequence,
        "arrival_delay_seconds": event.arrival_delay_seconds,
        "departure_delay_seconds": event.departure_delay_seconds,
    }


def _event_from_json(event: dict[str, Any]) -> StopTimeEvent:
    return StopTimeEvent(
        stop_id=event["stop_id"],
        stop_sequence=event["stop_sequence"],
        arrival_delay_seconds=event["arrival_delay_seconds"],
        departure_delay_seconds=event["departure_delay_seconds"],
    )
