import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask

from carma.domain.models import BoundingBox, FeedStatus, TripId, VehiclePosition
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


COMPUTED_AT = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def _position(trip_id: str = "T1") -> VehiclePosition:
    return VehiclePosition(
        trip_id=TripId(trip_id),
        route_id="R1",
        route_short_name="M1",
        lat=52.52,
        lon=13.41,
        bearing=90.0,
        delay_seconds=120,
        computed_at=COMPUTED_AT,
    )


class StubPositionReader:
    """Records the query the endpoint derived from the request."""

    def __init__(self, rows: tuple[VehiclePosition, ...]) -> None:
        self.rows = rows
        self.calls: list[tuple[BoundingBox | None, int]] = []
        self.since_calls: list[tuple[datetime | None, int]] = []

    def positions(self, bbox: BoundingBox | None, limit: int) -> tuple[VehiclePosition, ...]:
        self.calls.append((bbox, limit))
        return self.rows[:limit]

    def positions_since(
        self, cursor: datetime | None, limit: int
    ) -> tuple[VehiclePosition, ...]:
        self.since_calls.append((cursor, limit))
        if cursor is not None and cursor >= COMPUTED_AT:
            return ()
        return self.rows[:limit]


def _positions_app(reader: StubPositionReader) -> Flask:
    return create_app(
        feed_status_source=lambda: None,
        position_reader_factory=lambda: nullcontext(reader),
    )


def test_positions_returns_rows_with_route_names() -> None:
    reader = StubPositionReader((_position(),))
    client = _positions_app(reader).test_client()

    response = client.get("/api/v1/positions")

    assert response.status_code == 200
    body = response.get_json()
    assert body["count"] == 1
    row = body["positions"][0]
    assert row["trip_id"] == "T1"
    assert row["route_short_name"] == "M1"
    assert row["delay_seconds"] == 120
    assert row["computed_at"] == COMPUTED_AT.isoformat()
    assert reader.calls == [(None, 10_000)]


def test_positions_parses_bbox_and_caps_limit() -> None:
    reader = StubPositionReader((_position(),))
    client = _positions_app(reader).test_client()

    response = client.get("/api/v1/positions?bbox=13.2,52.4,13.6,52.6&limit=999999")

    assert response.status_code == 200
    bbox, limit = reader.calls[0]
    assert bbox == BoundingBox(min_lon=13.2, min_lat=52.4, max_lon=13.6, max_lat=52.6)
    assert limit == 20_000


@pytest.mark.parametrize(
    "query",
    [
        "bbox=13.2,52.4,13.6",  # wrong arity
        "bbox=a,b,c,d",  # not numbers
        "bbox=13.6,52.4,13.2,52.6",  # min > max
        "bbox=13.2,52.4,13.6,95.0",  # latitude out of range
        "limit=0",
        "limit=abc",
    ],
)
def test_positions_rejects_bad_parameters(query: str) -> None:
    client = _positions_app(StubPositionReader(())).test_client()

    response = client.get(f"/api/v1/positions?{query}")

    assert response.status_code == 400
    assert "error" in response.get_json()


def test_positions_stream_emits_snapshot_then_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CARMA_SSE_POLL_SECONDS", "0.01")
    reader = StubPositionReader((_position(),))
    client = _positions_app(reader).test_client()

    response = client.get("/api/v1/positions/stream")
    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    chunks = response.response  # the generator, pulled lazily
    first = next(iter(chunks))
    text = first if isinstance(first, str) else first.decode()
    assert text.startswith(f"id: {COMPUTED_AT.isoformat()}\nevent: positions\n")
    payload = json.loads(text.split("data: ", 1)[1])
    assert payload["cursor"] == COMPUTED_AT.isoformat()
    assert payload["positions"][0]["trip_id"] == "T1"
    response.close()
    # The delta query used the advanced cursor, so nothing is re-sent.
    assert reader.since_calls[0] == (None, 20_000)


def test_positions_stream_resumes_from_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CARMA_SSE_POLL_SECONDS", "0.01")
    reader = StubPositionReader((_position(),))
    client = _positions_app(reader).test_client()

    response = client.get(
        "/api/v1/positions/stream", headers={"Last-Event-ID": COMPUTED_AT.isoformat()}
    )
    first = next(iter(response.response))
    text = first if isinstance(first, str) else first.decode()
    assert text.startswith(":")  # nothing newer: keep-alive comment
    response.close()
    assert reader.since_calls[0] == (COMPUTED_AT, 20_000)


def test_positions_stream_rejects_malformed_cursor() -> None:
    client = _positions_app(StubPositionReader(())).test_client()

    response = client.get("/api/v1/positions/stream?cursor=not-a-time")

    assert response.status_code == 400
