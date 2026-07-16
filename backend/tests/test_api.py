from datetime import UTC, datetime, timedelta

from carma.domain.models import FeedStatus
from carma.entrypoints.api import create_app


def _status(age_seconds: float) -> FeedStatus:
    return FeedStatus(
        last_snapshot_at=datetime.now(tz=UTC) - timedelta(seconds=age_seconds),
        last_entity_count=6543,
    )


def test_health_returns_ok_with_feed_summary() -> None:
    client = create_app(feed_status_source=lambda: _status(age_seconds=10)).test_client()

    response = client.get("/health")

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "ok"
    assert body["feed"]["fresh"] is True
    assert body["feed"]["state"] == "fresh"


def test_health_stays_200_when_feed_is_stale() -> None:
    client = create_app(feed_status_source=lambda: _status(age_seconds=600)).test_client()

    response = client.get("/health")

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "ok"
    assert body["feed"]["fresh"] is False
    assert body["feed"]["state"] == "stale"


def test_health_stays_200_when_status_source_fails() -> None:
    def broken() -> FeedStatus | None:
        raise ConnectionError("db down")

    client = create_app(feed_status_source=broken).test_client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json()["feed"] == {"state": "unavailable", "fresh": False}


def test_feed_endpoint_reports_details() -> None:
    client = create_app(feed_status_source=lambda: _status(age_seconds=30)).test_client()

    response = client.get("/api/v1/feed")

    assert response.status_code == 200
    body = response.get_json()
    assert body["fresh"] is True
    assert body["last_entity_count"] == 6543
    assert 29 <= body["age_seconds"] <= 35
    assert body["freshness_window_seconds"] == 120


def test_feed_endpoint_before_first_snapshot() -> None:
    client = create_app(feed_status_source=lambda: None).test_client()

    response = client.get("/api/v1/feed")

    assert response.status_code == 200
    assert response.get_json() == {"state": "no_data", "fresh": False}


def test_meta_describes_the_feed() -> None:
    client = create_app(feed_status_source=lambda: None).test_client()

    response = client.get("/api/v1/meta")

    assert response.status_code == 200
    feed = response.get_json()["feed"]
    assert feed["provider"].startswith("VBB")
    assert feed["vehicle_positions"] == "derived"
