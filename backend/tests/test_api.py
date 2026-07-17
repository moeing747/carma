import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask

from carma.application.ports import OptimizationRequest
from carma.application.position_stream import PositionCursor
from carma.domain.headway import HeadwayPlan, build_plan
from carma.domain.models import (
    BoundingBox,
    Coordinate,
    FeedStatus,
    ScheduledStop,
    TripId,
    VehiclePosition,
)
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
CURSOR_T1 = f"{COMPUTED_AT.isoformat()}|T1"


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
        headsign="Hauptbahnhof",
    )


class StubPositionReader:
    """Records the query the endpoint derived from the request."""

    def __init__(self, rows: tuple[VehiclePosition, ...]) -> None:
        self.rows = rows
        self.calls: list[tuple[BoundingBox | None, int]] = []
        self.since_calls: list[tuple[PositionCursor | None, int]] = []

    def positions(self, bbox: BoundingBox | None, limit: int) -> tuple[VehiclePosition, ...]:
        self.calls.append((bbox, limit))
        return self.rows[:limit]

    def positions_since(
        self, cursor: PositionCursor | None, limit: int
    ) -> tuple[VehiclePosition, ...]:
        self.since_calls.append((cursor, limit))
        ordered = sorted(self.rows, key=lambda row: (row.computed_at, row.trip_id.value))
        if cursor is not None:
            ordered = [
                row
                for row in ordered
                if (row.computed_at, row.trip_id.value) > (cursor.computed_at, cursor.trip_id)
            ]
        return tuple(ordered[:limit])

    def position_for_trip(self, trip_id: TripId) -> VehiclePosition | None:
        for row in self.rows:
            if row.trip_id == trip_id:
                return row
        return None


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
    assert row["headsign"] == "Hauptbahnhof"
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
        "bbox=nan,52.4,13.6,52.6",  # float() accepts "nan"
        "bbox=13.2,52.4,inf,52.6",  # float() accepts "inf"
        "bbox=13.2,-inf,13.6,52.6",
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
    assert text.startswith(f"id: {CURSOR_T1}\nevent: positions\n")
    payload = json.loads(text.split("data: ", 1)[1])
    assert payload["cursor"] == CURSOR_T1
    assert payload["positions"][0]["trip_id"] == "T1"
    response.close()
    # The delta query used the advanced cursor, so nothing is re-sent.
    assert reader.since_calls[0] == (None, 20_000)


def test_positions_stream_resumes_from_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CARMA_SSE_POLL_SECONDS", "0.01")
    reader = StubPositionReader((_position(),))
    client = _positions_app(reader).test_client()

    response = client.get("/api/v1/positions/stream", headers={"Last-Event-ID": CURSOR_T1})
    first = next(iter(response.response))
    text = first if isinstance(first, str) else first.decode()
    assert text.startswith(":")  # nothing newer: keep-alive comment
    response.close()
    assert reader.since_calls[0] == (PositionCursor(COMPUTED_AT, "T1"), 20_000)


def test_positions_stream_resumes_within_one_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keyset regression: rows sharing the cursor's computed_at but with a
    later trip_id (a tick split by the read limit) must still be delivered."""
    monkeypatch.setenv("CARMA_SSE_POLL_SECONDS", "0.01")
    reader = StubPositionReader((_position("T1"), _position("T2")))
    client = _positions_app(reader).test_client()

    response = client.get("/api/v1/positions/stream", headers={"Last-Event-ID": CURSOR_T1})
    first = next(iter(response.response))
    text = first if isinstance(first, str) else first.decode()
    payload = json.loads(text.split("data: ", 1)[1])
    assert [row["trip_id"] for row in payload["positions"]] == ["T2"]
    assert payload["cursor"] == f"{COMPUTED_AT.isoformat()}|T2"
    response.close()


def test_positions_stream_accepts_a_bare_timestamp_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-keyset token shape still resumes (replaying that timestamp)."""
    monkeypatch.setenv("CARMA_SSE_POLL_SECONDS", "0.01")
    reader = StubPositionReader((_position(),))
    client = _positions_app(reader).test_client()

    response = client.get(
        "/api/v1/positions/stream", headers={"Last-Event-ID": COMPUTED_AT.isoformat()}
    )
    first = next(iter(response.response))
    text = first if isinstance(first, str) else first.decode()
    payload = json.loads(text.split("data: ", 1)[1])
    assert [row["trip_id"] for row in payload["positions"]] == ["T1"]
    response.close()
    assert reader.since_calls[0] == (PositionCursor(COMPUTED_AT, ""), 20_000)


def test_positions_stream_rejects_malformed_cursor() -> None:
    client = _positions_app(StubPositionReader(())).test_client()

    response = client.get("/api/v1/positions/stream?cursor=not-a-time")

    assert response.status_code == 400


def _stop(
    sequence: int, name: str, arrival: int | None, departure: int | None
) -> ScheduledStop:
    return ScheduledStop(
        stop_id=f"S{sequence}",
        stop_name=name,
        stop_sequence=sequence,
        arrival_seconds=arrival,
        departure_seconds=departure,
        coordinate=Coordinate(lat=52.5, lon=13.4),
    )


T1_SCHEDULE = (
    _stop(1, "Alpha", None, 8 * 3600),
    _stop(2, "Beta", 8 * 3600 + 600, 8 * 3600 + 660),
    # A past-midnight time: 25:10 must render as wall-clock 01:10.
    _stop(3, "Gamma", 25 * 3600 + 600, None),
)


class StubScheduleRepository:
    def __init__(self, schedules: dict[str, tuple[ScheduledStop, ...]]) -> None:
        self.schedules = schedules

    def active_trip_ids(self, at: datetime) -> frozenset[TripId]:
        return frozenset(TripId(trip_id) for trip_id in self.schedules)

    def schedule_for_trip(self, trip_id: TripId) -> tuple[ScheduledStop, ...]:
        return self.schedules.get(trip_id.value, ())

    def shape_for_trip(self, trip_id: TripId) -> tuple[Coordinate, ...] | None:
        return None


def _schedule_app(reader: StubPositionReader) -> Flask:
    return create_app(
        feed_status_source=lambda: None,
        position_reader_factory=lambda: nullcontext(reader),
        schedule_repository_factory=lambda: nullcontext(
            StubScheduleRepository({"T1": T1_SCHEDULE})
        ),
    )


def test_trip_schedule_returns_ordered_stops_with_delay() -> None:
    client = _schedule_app(StubPositionReader((_position("T1"),))).test_client()

    response = client.get("/api/v1/trips/T1/schedule")

    assert response.status_code == 200
    body = response.get_json()
    assert body["trip_id"] == "T1"
    assert body["delay_seconds"] == 120
    assert [stop["name"] for stop in body["stops"]] == ["Alpha", "Beta", "Gamma"]
    first, second, third = body["stops"]
    assert first["arrival"] is None and first["departure"] == "08:00"
    assert first["departure_seconds"] == 8 * 3600
    assert second["arrival"] == "08:10" and second["departure"] == "08:11"
    assert third["arrival"] == "01:10"  # 25:10 GTFS time, wall clock
    assert third["arrival_seconds"] == 25 * 3600 + 600


def test_trip_schedule_without_position_has_null_delay() -> None:
    client = _schedule_app(StubPositionReader(())).test_client()

    response = client.get("/api/v1/trips/T1/schedule")

    assert response.status_code == 200
    assert response.get_json()["delay_seconds"] is None


def test_trip_schedule_unknown_trip_is_404() -> None:
    client = _schedule_app(StubPositionReader(())).test_client()

    response = client.get("/api/v1/trips/NOPE/schedule")

    assert response.status_code == 404
    assert "error" in response.get_json()


class BrokenScheduleRepository:
    """Raises like postgis_schedule does on malformed data (no coordinates)."""

    def active_trip_ids(self, at: datetime) -> frozenset[TripId]:
        return frozenset()

    def schedule_for_trip(self, trip_id: TripId) -> tuple[ScheduledStop, ...]:
        raise ValueError("stop S1 on trip T1 has no coordinates")

    def shape_for_trip(self, trip_id: TripId) -> tuple[Coordinate, ...] | None:
        return None


def test_unhandled_errors_come_back_as_json_500() -> None:
    app = create_app(
        feed_status_source=lambda: None,
        position_reader_factory=lambda: nullcontext(StubPositionReader(())),
        schedule_repository_factory=lambda: nullcontext(BrokenScheduleRepository()),
    )

    response = app.test_client().get("/api/v1/trips/T1/schedule")

    assert response.status_code == 500
    assert response.get_json() == {"error": "internal server error"}


def test_unknown_routes_come_back_as_json_404() -> None:
    client = create_app(feed_status_source=lambda: None).test_client()

    response = client.get("/api/v1/nope")

    assert response.status_code == 404
    assert "error" in response.get_json()


# ---- POST /api/v1/optimize ----

OPTIMIZE_AT = datetime(2026, 7, 16, 8, 15)  # naive feed-local, mid-pattern

# 08:00 -> 08:10/08:11 -> 08:20 pattern shared by the optimize test trips.
OPTIMIZE_PATTERN: tuple[ScheduledStop, ...] = (
    _stop(1, "Alpha", None, 8 * 3600),
    _stop(2, "Beta", 8 * 3600 + 600, 8 * 3600 + 660),
    _stop(3, "Gamma", 8 * 3600 + 1200, None),
)


class FakeOptimizationEngine:
    name = "fake"

    def solve(self, request: OptimizationRequest) -> HeadwayPlan:
        # Hold the tail vehicle 60s; enough plan shape to exercise the wire.
        holds = [0] * (len(request.vehicles) - 1) + [60]
        return build_plan(request.vehicles, holds)


def _line_position(trip_id: str, delay: int, headsign: str = "North") -> VehiclePosition:
    return VehiclePosition(
        trip_id=TripId(trip_id),
        route_id="R1",
        route_short_name="M1",
        lat=52.52,
        lon=13.41,
        bearing=None,
        delay_seconds=delay,
        computed_at=COMPUTED_AT,
        headsign=headsign,
    )


def _optimize_app(
    rows: tuple[VehiclePosition, ...],
    engine: FakeOptimizationEngine | None = None,
) -> Flask:
    schedules = {row.trip_id.value: OPTIMIZE_PATTERN for row in rows}
    return create_app(
        feed_status_source=lambda: None,
        position_reader_factory=lambda: nullcontext(StubPositionReader(rows)),
        schedule_repository_factory=lambda: nullcontext(StubScheduleRepository(schedules)),
        optimization_engine=engine if engine is not None else FakeOptimizationEngine(),
        now_source=lambda: OPTIMIZE_AT,
    )


BUNCHED_LINE = (
    _line_position("A", delay=0),  # position 900
    _line_position("B", delay=780),  # position 120 (bunched onto C)
    _line_position("C", delay=840),  # position 60
)


def test_optimize_returns_the_advisory_plan() -> None:
    client = _optimize_app(BUNCHED_LINE).test_client()

    response = client.post("/api/v1/optimize", json={"route_short_name": "M1"})

    assert response.status_code == 200
    body = response.get_json()
    assert body["route_short_name"] == "M1"
    assert body["direction"] == "North"
    assert body["engine"] == "fake"
    assert [row["trip_id"] for row in body["vehicles"]] == ["A", "B", "C"]
    assert [row["hold_seconds"] for row in body["vehicles"]] == [0, 0, 60]
    leader, middle, tail = body["vehicles"]
    assert leader["headway_before_seconds"] is None
    assert middle["headway_before_seconds"] == 780.0
    assert (tail["headway_before_seconds"], tail["headway_after_seconds"]) == (60.0, 120.0)
    assert tail["next_stop_name"] == "Beta"
    summary = body["summary"]
    assert summary["vehicle_count"] == 3
    assert summary["max_hold_seconds"] == 300
    assert (
        summary["headway_stddev_after_seconds"] < summary["headway_stddev_before_seconds"]
    )


def test_optimize_unknown_line_is_404() -> None:
    client = _optimize_app(BUNCHED_LINE).test_client()

    response = client.post("/api/v1/optimize", json={"route_short_name": "M99"})

    assert response.status_code == 404
    assert "M99" in response.get_json()["error"]


def test_optimize_thin_line_is_422_with_reason() -> None:
    client = _optimize_app(BUNCHED_LINE[:2]).test_client()

    response = client.post("/api/v1/optimize", json={"route_short_name": "M1"})

    assert response.status_code == 422
    error = response.get_json()["error"]
    assert "only 2" in error and "at least 3" in error


@pytest.mark.parametrize(
    "body",
    [{}, {"route_short_name": ""}, {"route_short_name": 42}, "M1"],
)
def test_optimize_rejects_malformed_bodies(body: object) -> None:
    client = _optimize_app(BUNCHED_LINE).test_client()

    response = client.post("/api/v1/optimize", json=body)

    assert response.status_code == 400
    assert "error" in response.get_json()


def test_optimize_rejects_a_non_json_body() -> None:
    client = _optimize_app(BUNCHED_LINE).test_client()

    response = client.post("/api/v1/optimize", data="M1")

    assert response.status_code == 400
    assert "error" in response.get_json()


def test_optimizer_engine_selected_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CARMA_OPTIMIZER", "heuristic")
    app = create_app(
        feed_status_source=lambda: None,
        position_reader_factory=lambda: nullcontext(StubPositionReader(BUNCHED_LINE)),
        schedule_repository_factory=lambda: nullcontext(
            StubScheduleRepository({row.trip_id.value: OPTIMIZE_PATTERN for row in BUNCHED_LINE})
        ),
        now_source=lambda: OPTIMIZE_AT,
    )

    response = app.test_client().post("/api/v1/optimize", json={"route_short_name": "M1"})

    assert response.status_code == 200
    assert response.get_json()["engine"] == "heuristic"


def test_unknown_optimizer_env_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CARMA_OPTIMIZER", "quantum")

    with pytest.raises(ValueError, match="CARMA_OPTIMIZER"):
        create_app(feed_status_source=lambda: None)
