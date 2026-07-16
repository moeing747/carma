"""Testcontainers PostGIS: the set-based position engine end to end.

Loads the mini GTFS fixture, injects synthetic trip_delays, runs recompute
ticks, and — the core assertion — holds the SQL engine's output against the
pure reference implementation in carma.domain.positioning (equivalence:
positions within meters, identical delays, bearings within a degree).

Fixture timetable (Europe/Berlin, service day Tue 2026-07-14 for T1/T2):
  T1 (route R1 "M1", shape SH1): S1 08:00 -> S2 08:10/08:11 -> S3
     08:20/08:21 -> S4 08:30
  T2 (shapeless, crosses midnight): S4 23:50 -> S5 24:30/24:31 -> S6 25:10
  T3 (service SPECIAL, Sat 2026-07-18): S1 10:00 -> S5 10:10/10:11 -> S6 10:20

Tests in this module run in order and share the container (same pattern as
test_postgis_integration.py); each test recomputes the state it needs.
"""

import json
import time
from collections.abc import Iterator
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from testcontainers.postgres import PostgresContainer

from carma.adapters.gtfs_static import load_gtfs_zip
from carma.adapters.migrations import apply_migrations
from carma.adapters.postgis_delays import PostgisTripDelayRepository
from carma.adapters.postgis_positions import (
    PostgisPositionEngine,
    PostgisVehiclePositionReader,
)
from carma.adapters.postgis_schedule import PostgisTripScheduleRepository
from carma.domain.models import BoundingBox, StopTimeEvent, TripDelay, TripId
from carma.domain.positioning import derive_position
from carma.entrypoints.api import create_app
from tests.gtfs_fixture import write_fixture_zip

pytestmark = pytest.mark.integration

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"

# T1's service day; 08:15 falls mid-trip.
SERVICE_DAY = datetime(2026, 7, 14)

# Generous CI bound; the interesting number is the live one in the smoke
# report (budget: a full Berlin tick comfortably under the 5s interval).
PERF_BUDGET_SECONDS = 3.0

EQUIVALENCE_TOLERANCE_METERS = 5.0
BEARING_TOLERANCE_DEGREES = 2.0


@pytest.fixture(scope="module")
def conn(tmp_path_factory: pytest.TempPathFactory) -> Iterator[psycopg.Connection[Any]]:
    fixture_zip = write_fixture_zip(tmp_path_factory.mktemp("gtfs") / "gtfs-mini.zip")
    with PostgresContainer("postgis/postgis:16-3.4") as container:
        url = (
            f"postgresql://{container.username}:{container.password}"
            f"@{container.get_container_host_ip()}:{container.get_exposed_port(5432)}"
            f"/{container.dbname}"
        )
        # autocommit: engine and delay repository run their own transactions.
        with psycopg.connect(url, autocommit=True) as connection:
            apply_migrations(connection, MIGRATIONS_DIR)
            load_gtfs_zip(connection, fixture_zip)
            yield connection


@pytest.fixture()
def engine(conn: psycopg.Connection[Any]) -> PostgisPositionEngine:
    return PostgisPositionEngine(conn=conn)


@pytest.fixture()
def repo(conn: psycopg.Connection[Any]) -> PostgisTripScheduleRepository:
    return PostgisTripScheduleRepository(conn=conn)


def _at(hour: int, minute: int, second: int = 0, day: int = 14) -> datetime:
    return datetime(2026, 7, day, hour, minute, second)


def _seconds(at: datetime) -> int:
    """Feed-local instant -> seconds into T1/T2's service day (2026-07-14)."""
    midnight = SERVICE_DAY
    return int((at - midnight).total_seconds())


def _set_delays(
    conn: psycopg.Connection[Any], trip_id: str, events: tuple[StopTimeEvent, ...]
) -> None:
    conn.execute("DELETE FROM trip_delays")
    PostgisTripDelayRepository(conn=conn).save(
        TripDelay(
            trip_id=TripId(trip_id),
            route_id="R1",
            timestamp=datetime.now(tz=UTC),
            stop_time_events=events,
        )
    )


def _clear_delays(conn: psycopg.Connection[Any]) -> None:
    conn.execute("DELETE FROM trip_delays")


def _stored(
    conn: psycopg.Connection[Any], trip_id: str
) -> tuple[float, float, float | None, int] | None:
    row = conn.execute(
        "SELECT ST_Y(geom), ST_X(geom), bearing, delay_seconds"
        " FROM vehicle_positions WHERE trip_id = %s",
        (trip_id,),
    ).fetchone()
    return None if row is None else (row[0], row[1], row[2], row[3])


def _meters_between(
    conn: psycopg.Connection[Any], lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    row = conn.execute(
        "SELECT ST_Distance(ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,"
        " ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography)",
        (lon1, lat1, lon2, lat2),
    ).fetchone()
    assert row is not None
    return float(row[0])


def _bearing_delta(a: float | None, b: float | None) -> float:
    if a is None or b is None:
        return 0.0 if a == b else 360.0
    diff = abs(a - b) % 360.0
    return min(diff, 360.0 - diff)


def _assert_equivalent(
    conn: psycopg.Connection[Any],
    repo: PostgisTripScheduleRepository,
    trip_id: str,
    events: tuple[StopTimeEvent, ...],
    at: datetime,
) -> None:
    reference = derive_position(
        repo.schedule_for_trip(TripId(trip_id)),
        events,
        repo.shape_for_trip(TripId(trip_id)),
        _seconds(at),
    )
    stored = _stored(conn, trip_id)
    if reference is None:
        assert stored is None, f"{trip_id}@{at}: SQL produced a position, reference did not"
        return
    assert stored is not None, f"{trip_id}@{at}: reference produced a position, SQL did not"
    lat, lon, bearing, delay = stored
    distance = _meters_between(
        conn, lat, lon, reference.coordinate.lat, reference.coordinate.lon
    )
    assert distance < EQUIVALENCE_TOLERANCE_METERS, f"{trip_id}@{at}: {distance:.1f}m apart"
    assert delay == reference.delay_seconds, f"{trip_id}@{at}"
    assert _bearing_delta(bearing, reference.bearing) < BEARING_TOLERANCE_DEGREES, (
        f"{trip_id}@{at}: bearing {bearing} vs reference {reference.bearing}"
    )


T1_DELAY_EVENTS = (
    StopTimeEvent(
        stop_id="S2", stop_sequence=2, arrival_delay_seconds=240, departure_delay_seconds=300
    ),
)


def test_schedule_only_trip_gets_a_position_on_its_shape(
    conn: psycopg.Connection[Any], engine: PostgisPositionEngine
) -> None:
    _clear_delays(conn)
    written = engine.recompute({TripId("T1")}, _at(8, 15))

    assert written == 1
    row = conn.execute(
        "SELECT vp.route_id, ST_Distance(vp.geom::geography, sh.geom::geography)"
        " FROM vehicle_positions vp, shapes sh"
        " WHERE vp.trip_id = 'T1' AND sh.shape_id = 'SH1'"
    ).fetchone()
    assert row is not None
    route_id, meters_off_shape = row
    assert route_id == "R1"
    assert meters_off_shape < 5.0  # the derived point sits ON the shape


def test_fraction_cache_fills_lazily_and_is_monotonic(
    conn: psycopg.Connection[Any], engine: PostgisPositionEngine
) -> None:
    engine.recompute({TripId("T1")}, _at(8, 15))
    rows = conn.execute(
        "SELECT f.fraction FROM shape_stop_fractions f"
        " JOIN stop_times st ON st.stop_id = f.stop_id AND st.trip_id = 'T1'"
        " WHERE f.shape_id = 'SH1' ORDER BY st.stop_sequence"
    ).fetchall()
    fractions = [row[0] for row in rows]
    assert len(fractions) == 4
    assert fractions == sorted(fractions)
    assert fractions[0] == 0.0
    assert fractions[-1] == 1.0


def test_dwell_pins_the_vehicle_at_the_stop(
    conn: psycopg.Connection[Any], engine: PostgisPositionEngine
) -> None:
    _clear_delays(conn)
    engine.recompute({TripId("T1")}, _at(8, 10, 30))  # between S2 arr and dep

    stored = _stored(conn, "T1")
    assert stored is not None
    row = conn.execute("SELECT ST_Y(geom), ST_X(geom) FROM stops WHERE stop_id = 'S2'").fetchone()
    assert row is not None
    assert _meters_between(conn, stored[0], stored[1], row[0], row[1]) < 5.0


def test_delayed_trip_trails_its_schedule_only_position(
    conn: psycopg.Connection[Any], engine: PostgisPositionEngine
) -> None:
    at = _at(8, 12)
    _clear_delays(conn)
    engine.recompute({TripId("T1")}, at)
    on_time = _stored(conn, "T1")

    _set_delays(conn, "T1", T1_DELAY_EVENTS)
    engine.recompute({TripId("T1")}, at)
    delayed = _stored(conn, "T1")

    assert on_time is not None and delayed is not None
    assert delayed[3] == 240  # travelling toward S2's delayed arrival
    row = conn.execute(
        "SELECT ST_LineLocatePoint(sh.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326)),"
        "       ST_LineLocatePoint(sh.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))"
        " FROM shapes sh WHERE sh.shape_id = 'SH1'",
        (delayed[1], delayed[0], on_time[1], on_time[0]),
    ).fetchone()
    assert row is not None
    delayed_fraction, on_time_fraction = row
    assert delayed_fraction < on_time_fraction


def test_sql_engine_matches_the_pure_reference(
    conn: psycopg.Connection[Any],
    engine: PostgisPositionEngine,
    repo: PostgisTripScheduleRepository,
) -> None:
    """The equivalence proof: schedule-only, delayed, early, dwelling,
    parked, past-scheduled-end, and shapeless midnight-crossing probes."""
    early_events = (
        StopTimeEvent(
            stop_id="S1", stop_sequence=1, arrival_delay_seconds=None, departure_delay_seconds=-90
        ),
    )
    late_start_events = (
        StopTimeEvent(
            stop_id="S1", stop_sequence=1, arrival_delay_seconds=240, departure_delay_seconds=240
        ),
    )
    # Probes stay inside the trip's *scheduled* span: that is the contract —
    # the engine is only handed trips the Phase 1 active-trip filter already
    # selected on exactly that span, and the reference assumes it.
    t1_probes: list[tuple[tuple[StopTimeEvent, ...], datetime]] = [
        (late_start_events, _at(8, 2)),  # parked at S1: origin departure delayed
        ((), _at(8, 5)),  # travelling S1 -> S2
        ((), _at(8, 10, 30)),  # dwelling at S2
        ((), _at(8, 25)),  # travelling S3 -> S4
        ((), _at(8, 29, 59)),  # about to arrive
        (T1_DELAY_EVENTS, _at(8, 12)),  # delayed: still short of S2
        (T1_DELAY_EVENTS, _at(8, 15)),  # delayed: dwelling at S2 (08:14-08:16)
        (T1_DELAY_EVENTS, _at(8, 28)),  # delayed: trailing on the last segment
        (early_events, _at(8, 9)),  # early vehicle, negative delay
    ]
    for events, at in t1_probes:
        if events:
            _set_delays(conn, "T1", events)
        else:
            _clear_delays(conn)
        engine.recompute({TripId("T1")}, at)
        _assert_equivalent(conn, repo, "T1", events, at)

    # Shapeless straight-line fallback, across midnight (T2, previous
    # service day): pinned cases have no bearing, travelling ones do.
    t2_events = (
        StopTimeEvent(
            stop_id="S5", stop_sequence=2, arrival_delay_seconds=120, departure_delay_seconds=120
        ),
    )
    t2_probes: list[tuple[tuple[StopTimeEvent, ...], datetime]] = [
        ((), _at(23, 55)),  # before midnight
        ((), _at(0, 30, 0, day=15)),  # after midnight, same service day
        (t2_events, _at(0, 31, 0, day=15)),
    ]
    for events, at in t2_probes:
        if events:
            _set_delays(conn, "T2", events)
        else:
            _clear_delays(conn)
        engine.recompute({TripId("T2")}, at)
        _assert_equivalent(conn, repo, "T2", events, at)


def test_trip_past_its_effective_end_is_removed(
    conn: psycopg.Connection[Any], engine: PostgisPositionEngine
) -> None:
    _clear_delays(conn)
    engine.recompute({TripId("T1")}, _at(8, 15))
    assert _stored(conn, "T1") is not None

    written = engine.recompute({TripId("T1")}, _at(8, 45))
    assert written == 0
    assert _stored(conn, "T1") is None


def test_vehicle_moves_between_ticks(
    conn: psycopg.Connection[Any], engine: PostgisPositionEngine
) -> None:
    _clear_delays(conn)
    engine.recompute({TripId("T1")}, _at(8, 5))
    first = _stored(conn, "T1")
    engine.recompute({TripId("T1")}, _at(8, 5, 15))
    second = _stored(conn, "T1")

    assert first is not None and second is not None
    moved = _meters_between(conn, first[0], first[1], second[0], second[1])
    # S1->S2 is ~750m in 600s: 15s is ~19m of progress along the shape.
    assert 5.0 < moved < 100.0


def test_recompute_tick_stays_within_budget(
    conn: psycopg.Connection[Any], engine: PostgisPositionEngine
) -> None:
    _clear_delays(conn)
    trips = {TripId("T1"), TripId("T2"), TripId("T3")}
    started = time.monotonic()
    engine.recompute(trips, _at(8, 15))
    elapsed = time.monotonic() - started
    assert elapsed < PERF_BUDGET_SECONDS, f"tick took {elapsed:.2f}s"


def test_positions_endpoint_filters_by_bbox(
    conn: psycopg.Connection[Any],
    engine: PostgisPositionEngine,
    repo: PostgisTripScheduleRepository,
) -> None:
    _set_delays(conn, "T1", T1_DELAY_EVENTS)
    engine.recompute({TripId("T1")}, _at(8, 12))
    reader = PostgisVehiclePositionReader(conn=conn)
    app = create_app(
        feed_status_source=lambda: None,
        position_reader_factory=lambda: nullcontext(reader),
        schedule_repository_factory=lambda: nullcontext(repo),
    )
    client = app.test_client()

    berlin = client.get("/api/v1/positions?bbox=13.3,52.4,13.5,52.6").get_json()
    assert berlin["count"] == 1
    row = berlin["positions"][0]
    assert row["trip_id"] == "T1"
    assert row["route_short_name"] == "M1"
    assert row["headsign"] == "Hauptbahnhof"

    elsewhere = client.get("/api/v1/positions?bbox=13.6,52.4,13.8,52.6").get_json()
    assert elsewhere["count"] == 0

    # The schedule route over the same fixture data: ordered stops with
    # display times, plus the live delay the recompute just stored.
    schedule = client.get("/api/v1/trips/T1/schedule")
    assert schedule.status_code == 200
    body = schedule.get_json()
    assert body["delay_seconds"] == 240
    assert [stop["name"] for stop in body["stops"]] == [
        "Alexanderplatz",
        "Hackescher Markt",
        "Friedrichstr.",
        "Hauptbahnhof",
    ]
    assert body["stops"][0]["departure"] == "08:00"
    assert body["stops"][1]["arrival_seconds"] == 8 * 3600 + 600

    assert client.get("/api/v1/trips/NOPE/schedule").status_code == 404
    _clear_delays(conn)


def test_sse_stream_delivers_a_delta_after_a_recompute(
    conn: psycopg.Connection[Any],
    engine: PostgisPositionEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CARMA_SSE_POLL_SECONDS", "0.05")
    _clear_delays(conn)
    engine.recompute({TripId("T1")}, _at(8, 15))
    reader = PostgisVehiclePositionReader(conn=conn)
    app = create_app(
        feed_status_source=lambda: None,
        position_reader_factory=lambda: nullcontext(reader),
    )
    client = app.test_client()

    response = client.get("/api/v1/positions/stream")
    chunks = iter(response.response)

    def next_text() -> str:
        chunk = next(chunks)
        return chunk if isinstance(chunk, str) else chunk.decode()

    first = next_text()  # initial snapshot
    assert "event: positions" in first
    snapshot = json.loads(first.split("data: ", 1)[1])
    assert snapshot["positions"][0]["trip_id"] == "T1"

    # A recompute between polls surfaces as a delta with a newer cursor.
    engine.recompute({TripId("T1")}, _at(8, 16))
    delta_text = ""
    for _ in range(50):  # keep-alives until the poll picks the change up
        delta_text = next_text()
        if delta_text.startswith("id:"):
            break
    delta = json.loads(delta_text.split("data: ", 1)[1])
    assert delta["positions"][0]["trip_id"] == "T1"
    assert delta["cursor"] > snapshot["cursor"]
    response.close()


def test_reader_positions_since_respects_the_cursor(
    conn: psycopg.Connection[Any], engine: PostgisPositionEngine
) -> None:
    _clear_delays(conn)
    engine.recompute({TripId("T1")}, _at(8, 15))
    reader = PostgisVehiclePositionReader(conn=conn)

    everything = reader.positions_since(None, 100)
    assert [row.trip_id for row in everything] == [TripId("T1")]
    cursor = everything[-1].computed_at
    assert reader.positions_since(cursor, 100) == ()

    engine.recompute({TripId("T1")}, _at(8, 16))
    newer = reader.positions_since(cursor, 100)
    assert [row.trip_id for row in newer] == [TripId("T1")]
    assert newer[0].computed_at > cursor

    inside = reader.positions(
        BoundingBox(min_lon=13.3, min_lat=52.4, max_lon=13.5, max_lat=52.6), 100
    )
    assert len(inside) == 1


def test_gtfs_reload_truncates_the_projection_tables(
    conn: psycopg.Connection[Any],
    engine: PostgisPositionEngine,
    tmp_path: Path,
) -> None:
    # LAST in the module: reloading resets the shared projection state.
    engine.recompute({TripId("T1")}, _at(8, 15))
    load_gtfs_zip(conn, write_fixture_zip(tmp_path / "gtfs-mini.zip"))
    row = conn.execute(
        "SELECT (SELECT count(*) FROM vehicle_positions),"
        " (SELECT count(*) FROM shape_stop_fractions)"
    ).fetchone()
    assert row == (0, 0)
