"""Testcontainers PostGIS integration: migrations, loader, and repository."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql
from testcontainers.postgres import PostgresContainer

from carma.adapters.gtfs_static import load_gtfs_zip
from carma.adapters.migrations import apply_migrations
from carma.adapters.postgis_schedule import PostgisTripScheduleRepository
from carma.domain.models import TripId
from tests.gtfs_fixture import EXPECTED_ROW_COUNTS, write_fixture_zip

pytestmark = pytest.mark.integration

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


@pytest.fixture(scope="module")
def conn(tmp_path_factory: pytest.TempPathFactory) -> Iterator[psycopg.Connection[Any]]:
    fixture_zip = write_fixture_zip(tmp_path_factory.mktemp("gtfs") / "gtfs-mini.zip")
    with PostgresContainer("postgis/postgis:16-3.4") as container:
        url = (
            f"postgresql://{container.username}:{container.password}"
            f"@{container.get_container_host_ip()}:{container.get_exposed_port(5432)}"
            f"/{container.dbname}"
        )
        with psycopg.connect(url) as connection:
            apply_migrations(connection, MIGRATIONS_DIR)
            load_gtfs_zip(connection, fixture_zip)
            yield connection


@pytest.fixture()
def repo(conn: psycopg.Connection[Any]) -> PostgisTripScheduleRepository:
    return PostgisTripScheduleRepository(conn=conn)


def _count(conn: psycopg.Connection[Any], table: str) -> int:
    query = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
    row = conn.execute(query).fetchone()
    assert row is not None
    return int(row[0])


def test_loads_expected_row_counts(conn: psycopg.Connection[Any]) -> None:
    for table, expected in EXPECTED_ROW_COUNTS.items():
        assert _count(conn, table) == expected, table


def test_migrations_are_idempotent(conn: psycopg.Connection[Any]) -> None:
    assert apply_migrations(conn, MIGRATIONS_DIR) == []


def test_reload_is_idempotent(
    conn: psycopg.Connection[Any], tmp_path: Path
) -> None:
    report = load_gtfs_zip(conn, write_fixture_zip(tmp_path / "gtfs-mini.zip"))
    assert report.rows_loaded == EXPECTED_ROW_COUNTS
    for table, expected in EXPECTED_ROW_COUNTS.items():
        assert _count(conn, table) == expected, table


def test_shape_assembled_as_linestring(conn: psycopg.Connection[Any]) -> None:
    row = conn.execute(
        "SELECT GeometryType(geom), ST_SRID(geom), ST_NPoints(geom)"
        " FROM shapes WHERE shape_id = 'SH1'"
    ).fetchone()
    assert row == ("LINESTRING", 4326, 5)
    # The staging table is drained after assembly.
    assert _count(conn, "shape_points") == 0


def test_stops_are_points_with_srid(conn: psycopg.Connection[Any]) -> None:
    row = conn.execute(
        "SELECT ST_SRID(geom), ST_Y(geom), ST_X(geom) FROM stops WHERE stop_id = 'S1'"
    ).fetchone()
    assert row is not None
    srid, lat, lon = row
    assert srid == 4326
    assert (lat, lon) == (pytest.approx(52.5219), pytest.approx(13.4132))


def test_active_trips_on_a_weekday(repo: PostgisTripScheduleRepository) -> None:
    # Tuesday 2026-07-14 08:15 local: T1 (08:00-08:30) runs, T2/T3 do not.
    assert repo.active_trip_ids(datetime(2026, 7, 14, 8, 15)) == {TripId("T1")}


def test_aware_instant_converts_to_feed_timezone(repo: PostgisTripScheduleRepository) -> None:
    # 06:15 UTC == 08:15 in Europe/Berlin during CEST.
    assert repo.active_trip_ids(datetime(2026, 7, 14, 6, 15, tzinfo=UTC)) == {TripId("T1")}


def test_calendar_dates_removal_suppresses_service(repo: PostgisTripScheduleRepository) -> None:
    # Wednesday 2026-07-15 is removed for WEEK via exception_type 2.
    assert repo.active_trip_ids(datetime(2026, 7, 15, 8, 15)) == frozenset()


def test_calendar_dates_addition_activates_service(repo: PostgisTripScheduleRepository) -> None:
    # Saturday 2026-07-18: WEEK's weekly pattern is off, SPECIAL is added.
    assert repo.active_trip_ids(datetime(2026, 7, 18, 10, 10)) == {TripId("T3")}


def test_midnight_crossing_trip_active_before_and_after_midnight(
    repo: PostgisTripScheduleRepository,
) -> None:
    assert repo.active_trip_ids(datetime(2026, 7, 14, 23, 55)) == {TripId("T2")}
    # 00:30 on the 15th: T2 belongs to the previous (Tuesday) service day.
    # The 15th itself is exception-removed, so nothing else runs.
    assert repo.active_trip_ids(datetime(2026, 7, 15, 0, 30)) == {TripId("T2")}


def test_no_trips_outside_service_window(repo: PostgisTripScheduleRepository) -> None:
    assert repo.active_trip_ids(datetime(2026, 8, 5, 8, 15)) == frozenset()


def test_schedule_for_trip_is_ordered_with_coordinates(
    repo: PostgisTripScheduleRepository,
) -> None:
    schedule = repo.schedule_for_trip(TripId("T1"))
    assert [stop.stop_id for stop in schedule] == ["S1", "S2", "S3", "S4"]
    assert [stop.stop_sequence for stop in schedule] == [1, 2, 3, 4]
    assert schedule[0].arrival_seconds == 8 * 3600
    assert schedule[0].coordinate.lat == pytest.approx(52.5219)
    assert schedule[0].coordinate.lon == pytest.approx(13.4132)


def test_schedule_preserves_times_past_midnight(repo: PostgisTripScheduleRepository) -> None:
    schedule = repo.schedule_for_trip(TripId("T2"))
    assert [stop.stop_id for stop in schedule] == ["S4", "S5", "S6"]
    assert schedule[0].departure_seconds == 23 * 3600 + 50 * 60
    assert schedule[-1].arrival_seconds == 25 * 3600 + 10 * 60


def test_schedule_for_unknown_trip_is_empty(repo: PostgisTripScheduleRepository) -> None:
    assert repo.schedule_for_trip(TripId("NOPE")) == ()


def test_shape_for_trip_returns_ordered_coordinates(
    repo: PostgisTripScheduleRepository,
) -> None:
    shape = repo.shape_for_trip(TripId("T1"))
    assert shape is not None
    assert len(shape) == 5
    assert shape[0].lat == pytest.approx(52.5219)
    assert shape[0].lon == pytest.approx(13.4132)
    assert shape[-1].lat == pytest.approx(52.5250)
    assert shape[-1].lon == pytest.approx(13.3694)


def test_shape_for_shapeless_trip_is_none(repo: PostgisTripScheduleRepository) -> None:
    assert repo.shape_for_trip(TripId("T2")) is None
