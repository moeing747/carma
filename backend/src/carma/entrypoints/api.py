import os
from collections.abc import Callable
from datetime import UTC, datetime

import psycopg
from flask import Flask

from carma.adapters.postgis_delays import PostgisFeedStatusRepository
from carma.domain.feed_health import FRESHNESS_WINDOW, feed_age_seconds, is_feed_fresh
from carma.domain.models import FeedStatus

FEED_META = {
    "provider": "VBB (Verkehrsverbund Berlin-Brandenburg)",
    "url": "https://production.gtfsrt.vbb.de/data",
    "entity_types": ["trip_update"],
    # The feed has no VehiclePositions; Carma derives them from TripUpdates.
    "vehicle_positions": "derived",
}

FeedStatusSource = Callable[[], FeedStatus | None]


def create_app(feed_status_source: FeedStatusSource | None = None) -> Flask:
    app = Flask("carma")
    source = feed_status_source if feed_status_source is not None else _feed_status_from_env

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

    return app


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


def _require_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url
