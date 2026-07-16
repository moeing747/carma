import json
import os
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime

import psycopg
from flask import Flask, Response, request

from carma.adapters.postgis_delays import PostgisFeedStatusRepository
from carma.adapters.postgis_positions import PostgisVehiclePositionReader
from carma.adapters.postgis_schedule import PostgisTripScheduleRepository
from carma.application.ports import TripScheduleRepository, VehiclePositionReader
from carma.application.position_stream import advance_cursor
from carma.domain.feed_health import FRESHNESS_WINDOW, feed_age_seconds, is_feed_fresh
from carma.domain.models import (
    BoundingBox,
    FeedStatus,
    ScheduledStop,
    TripId,
    VehiclePosition,
)

FEED_META = {
    "provider": "VBB (Verkehrsverbund Berlin-Brandenburg)",
    "url": "https://production.gtfsrt.vbb.de/data",
    "entity_types": ["trip_update"],
    # The feed has no VehiclePositions; Carma derives them from TripUpdates.
    "vehicle_positions": "derived",
}

# All of VBB peaks around ~7k concurrently active trips; the default returns
# the full fleet, the cap keeps a typo'd limit from asking for millions.
DEFAULT_POSITIONS_LIMIT = 10_000
MAX_POSITIONS_LIMIT = 20_000

FeedStatusSource = Callable[[], FeedStatus | None]
# A context manager per use: the default implementation opens a short-lived
# database connection scoped to one request (or one SSE client).
PositionReaderFactory = Callable[[], AbstractContextManager[VehiclePositionReader]]
ScheduleRepositoryFactory = Callable[[], AbstractContextManager[TripScheduleRepository]]


def create_app(
    feed_status_source: FeedStatusSource | None = None,
    position_reader_factory: PositionReaderFactory | None = None,
    schedule_repository_factory: ScheduleRepositoryFactory | None = None,
) -> Flask:
    app = Flask("carma")
    source = feed_status_source if feed_status_source is not None else _feed_status_from_env
    reader_factory = (
        position_reader_factory if position_reader_factory is not None else _reader_from_env
    )
    schedule_factory = (
        schedule_repository_factory
        if schedule_repository_factory is not None
        else _schedule_repository_from_env
    )

    @app.get("/health")
    def health() -> dict[str, object]:
        """Process liveness plus a feed-freshness summary.

        Always 200 while the process serves requests. The compose healthcheck
        gates on this endpoint, and a stale upstream feed (or an unreachable
        status table) is a data condition, not a process failure: returning
        non-200 for it would flap the healthcheck and restart a perfectly
        healthy API. Freshness details live in the body and on /api/v1/feed.
        """
        return {"status": "ok", "feed": _feed_report(source)}

    @app.get("/api/v1/feed")
    def feed() -> dict[str, object]:
        """Feed ingestion status; 200 even when stale (see /health rationale)."""
        return _feed_report(source)

    @app.get("/api/v1/meta")
    def meta() -> dict[str, object]:
        return {"feed": FEED_META}

    @app.get("/api/v1/positions")
    def positions() -> tuple[dict[str, object], int] | dict[str, object]:
        """Current derived vehicle positions.

        Query parameters:
        - ``bbox=minLon,minLat,maxLon,maxLat`` — optional spatial filter;
        - ``limit`` — max rows (default 10000, capped at 20000).
        """
        try:
            bbox = _parse_bbox(request.args.get("bbox"))
            limit = _parse_limit(request.args.get("limit"))
        except ValueError as error:
            return {"error": str(error)}, 400
        with reader_factory() as reader:
            rows = reader.positions(bbox, limit)
        return {
            "positions": [_position_json(row) for row in rows],
            "count": len(rows),
            "limit": limit,
        }

    @app.get("/api/v1/trips/<trip_id>/schedule")
    def trip_schedule(trip_id: str) -> tuple[dict[str, object], int] | dict[str, object]:
        """The trip's ordered stops plus its current delay, if one is known.

        Scheduled times come back both as raw GTFS seconds (may exceed 86400
        past midnight — the value clients should compare against) and as a
        wall-clock ``HH:MM`` display string.
        """
        with schedule_factory() as schedule:
            stops = schedule.schedule_for_trip(TripId(trip_id))
        if not stops:
            return {"error": f"unknown trip {trip_id!r}"}, 404
        with reader_factory() as reader:
            position = reader.position_for_trip(TripId(trip_id))
        return {
            "trip_id": trip_id,
            "delay_seconds": None if position is None else position.delay_seconds,
            "stops": [_stop_json(stop) for stop in stops],
        }

    @app.get("/api/v1/positions/stream")
    def positions_stream() -> Response | tuple[dict[str, object], int]:
        """SSE stream of position deltas.

        Each ``positions`` event carries the rows recomputed since the
        client's cursor plus the new cursor; the event ``id`` doubles as the
        cursor so EventSource reconnection (Last-Event-ID) resumes cleanly. A
        comment line is sent as keep-alive when nothing changed. Position
        *removals* (ended trips) are not announced — clients refresh via the
        snapshot endpoint or age vehicles out.

        Concurrency, honestly: each connected client occupies one gunicorn
        gthread worker thread (and one DB connection) for its whole lifetime.
        With the compose config (2 workers x 8 threads) that means a handful
        of concurrent SSE clients alongside normal requests — right for a
        demo dashboard, not for production fan-out (that would want an async
        server or a pub/sub edge).
        """
        raw_cursor = request.args.get("cursor") or request.headers.get("Last-Event-ID")
        try:
            cursor = datetime.fromisoformat(raw_cursor) if raw_cursor else None
        except ValueError:
            return {"error": "cursor must be an ISO-8601 timestamp"}, 400
        poll_seconds = float(os.environ.get("CARMA_SSE_POLL_SECONDS", "3"))
        return Response(
            _event_stream(reader_factory, cursor, poll_seconds),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def _event_stream(
    reader_factory: PositionReaderFactory,
    cursor: datetime | None,
    poll_seconds: float,
) -> Iterator[str]:
    # The first poll runs before any sleep, so a new client gets the current
    # state (or an immediate keep-alive) without waiting a full interval.
    with reader_factory() as reader:
        while True:
            rows = reader.positions_since(cursor, MAX_POSITIONS_LIMIT)
            newest = advance_cursor(cursor, rows)
            if rows and newest is not None:
                cursor = newest
                token = newest.isoformat()
                payload = json.dumps(
                    {
                        "positions": [_position_json(row) for row in rows],
                        "cursor": token,
                    }
                )
                yield f"id: {token}\nevent: positions\ndata: {payload}\n\n"
            else:
                yield ": keep-alive\n\n"
            time.sleep(poll_seconds)


def _position_json(row: VehiclePosition) -> dict[str, object]:
    return {
        "trip_id": row.trip_id.value,
        "route_id": row.route_id,
        "route_short_name": row.route_short_name,
        "headsign": row.headsign,
        "lat": row.lat,
        "lon": row.lon,
        "bearing": row.bearing,
        "delay_seconds": row.delay_seconds,
        "computed_at": row.computed_at.isoformat(),
    }


def _stop_json(stop: ScheduledStop) -> dict[str, object]:
    return {
        "stop_id": stop.stop_id,
        "name": stop.stop_name,
        "stop_sequence": stop.stop_sequence,
        "arrival_seconds": stop.arrival_seconds,
        "departure_seconds": stop.departure_seconds,
        "arrival": _hh_mm(stop.arrival_seconds),
        "departure": _hh_mm(stop.departure_seconds),
    }


def _hh_mm(seconds: int | None) -> str | None:
    """Service-day seconds -> wall-clock ``HH:MM`` (25:10:00 shows as 01:10)."""
    if seconds is None:
        return None
    return f"{seconds // 3600 % 24:02d}:{seconds // 60 % 60:02d}"


def _parse_bbox(raw: str | None) -> BoundingBox | None:
    if raw is None or raw == "":
        return None
    parts = raw.split(",")
    if len(parts) != 4:
        raise ValueError("bbox must be minLon,minLat,maxLon,maxLat")
    try:
        min_lon, min_lat, max_lon, max_lat = (float(part) for part in parts)
    except ValueError:
        raise ValueError("bbox coordinates must be numbers") from None
    return BoundingBox(min_lon=min_lon, min_lat=min_lat, max_lon=max_lon, max_lat=max_lat)


def _parse_limit(raw: str | None) -> int:
    if raw is None or raw == "":
        return DEFAULT_POSITIONS_LIMIT
    try:
        limit = int(raw)
    except ValueError:
        raise ValueError("limit must be an integer") from None
    if limit < 1:
        raise ValueError("limit must be positive")
    return min(limit, MAX_POSITIONS_LIMIT)


def _feed_report(source: FeedStatusSource) -> dict[str, object]:
    try:
        status = source()
    except Exception:
        # Degrade to "unavailable" instead of a 500: /health must stay up
        # while the status table or its connection is briefly unreachable.
        return {"state": "unavailable", "fresh": False}
    if status is None:
        return {"state": "no_data", "fresh": False}
    now = datetime.now(tz=UTC)
    fresh = is_feed_fresh(status.last_snapshot_at, now)
    return {
        "state": "fresh" if fresh else "stale",
        "fresh": fresh,
        "last_snapshot_at": status.last_snapshot_at.isoformat(),
        "last_entity_count": status.last_entity_count,
        "age_seconds": round(feed_age_seconds(status.last_snapshot_at, now), 1),
        "freshness_window_seconds": int(FRESHNESS_WINDOW.total_seconds()),
    }


def _feed_status_from_env() -> FeedStatus | None:
    # A short-lived connection per call: /health is probed every ~10s and a
    # hung pooled connection must not wedge liveness. connect_timeout bounds
    # the worst case well under the probe timeout.
    with psycopg.connect(_require_database_url(), connect_timeout=3) as conn:
        return PostgisFeedStatusRepository(conn=conn).latest()


@contextmanager
def _reader_from_env() -> Iterator[VehiclePositionReader]:
    with psycopg.connect(_require_database_url(), connect_timeout=3) as conn:
        yield PostgisVehiclePositionReader(conn=conn)


@contextmanager
def _schedule_repository_from_env() -> Iterator[TripScheduleRepository]:
    with psycopg.connect(_require_database_url(), connect_timeout=3) as conn:
        yield PostgisTripScheduleRepository(conn=conn)


def _require_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url
