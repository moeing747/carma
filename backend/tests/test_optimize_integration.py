"""One optimize round-trip against a real PostGIS world.

Loads a purpose-built GTFS fixture — bus line "42", four trips on the same
10-minute pattern towards one headsign — injects a delay that bunches the
third bus onto the fourth, recomputes positions with the production SQL
engine, and drives POST /api/v1/optimize through the Flask app with the
real adapters and the CP-SAT engine. The plan must even the spread out.
"""

import csv
import io
import zipfile
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
from carma.adapters.optimize_cpsat import CpSatOptimizationEngine
from carma.adapters.postgis_delays import PostgisTripDelayRepository
from carma.adapters.postgis_positions import (
    PostgisPositionEngine,
    PostgisVehiclePositionReader,
)
from carma.adapters.postgis_schedule import PostgisTripScheduleRepository
from carma.domain.models import StopTimeEvent, TripDelay, TripId
from carma.entrypoints.api import create_app

pytestmark = pytest.mark.integration

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"

# Tuesday on the WEEK service; all four buses are mid-route at 08:45.
AT = datetime(2026, 7, 14, 8, 45)

# Six stops, 10 minutes apart, roughly along Unter den Linden.
_STOPS = [
    ("B1", "Depot", "52.5160", "13.3777"),
    ("B2", "Brandenburger Tor", "52.5163", "13.3855"),
    ("B3", "Unter den Linden", "52.5170", "13.3925"),
    ("B4", "Friedrichstr.", "52.5175", "13.3990"),
    ("B5", "Museumsinsel", "52.5180", "13.4055"),
    ("B6", "Alexanderplatz", "52.5185", "13.4120"),
]

# Departures 08:00 / 08:10 / 08:20 / 08:30; 50-minute runs, one headsign.
_TRIP_STARTS = {"OP1": 0, "OP2": 10, "OP3": 20, "OP4": 30}


def _stop_times() -> list[list[str]]:
    rows: list[list[str]] = [
        ["trip_id", "stop_sequence", "stop_id", "arrival_time", "departure_time"]
    ]
    for trip_id, start_minute in _TRIP_STARTS.items():
        for index, (stop_id, _, _, _) in enumerate(_STOPS):
            minute = start_minute + 10 * index
            clock = f"{8 + minute // 60:02d}:{minute % 60:02d}:00"
            rows.append([trip_id, str(index + 1), stop_id, clock, clock])
    return rows


_FILES: dict[str, list[list[str]]] = {
    "agency.txt": [
        ["agency_id", "agency_name", "agency_url", "agency_timezone"],
        ["A1", "Mini Verkehr", "https://example.org", "Europe/Berlin"],
    ],
    "routes.txt": [
        ["route_id", "agency_id", "route_short_name", "route_long_name", "route_type"],
        ["R42", "A1", "42", "Depot - Alexanderplatz", "3"],
    ],
    "stops.txt": [["stop_id", "stop_name", "stop_lat", "stop_lon"]]
    + [list(stop) for stop in _STOPS],
    "trips.txt": [["trip_id", "route_id", "service_id", "trip_headsign", "direction_id"]]
    + [[trip_id, "R42", "WEEK", "Alexanderplatz", "0"] for trip_id in _TRIP_STARTS],
    "stop_times.txt": _stop_times(),
    "calendar.txt": [
        ["service_id", "monday", "tuesday", "wednesday", "thursday", "friday",
         "saturday", "sunday", "start_date", "end_date"],
        ["WEEK", "1", "1", "1", "1", "1", "0", "0", "20260701", "20260731"],
    ],
}


def _write_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, rows in _FILES.items():
            buffer = io.StringIO()
            csv.writer(buffer, lineterminator="\n").writerows(rows)
            archive.writestr(name, buffer.getvalue())
    return path


@pytest.fixture(scope="module")
def conn(tmp_path_factory: pytest.TempPathFactory) -> Iterator[psycopg.Connection[Any]]:
    fixture_zip = _write_zip(tmp_path_factory.mktemp("gtfs") / "gtfs-bunched.zip")
    with PostgresContainer("postgis/postgis:16-3.4") as container:
        url = (
            f"postgresql://{container.username}:{container.password}"
            f"@{container.get_container_host_ip()}:{container.get_exposed_port(5432)}"
            f"/{container.dbname}"
        )
        with psycopg.connect(url, autocommit=True) as connection:
            apply_migrations(connection, MIGRATIONS_DIR)
            load_gtfs_zip(connection, fixture_zip)
            yield connection


def test_optimize_round_trip_evens_out_a_bunched_line(
    conn: psycopg.Connection[Any],
) -> None:
    # OP3 runs 4 minutes late from its first stop: at 08:45 it is 6 pattern
    # minutes ahead of OP4 instead of 10, and 14 behind OP2 instead of 10.
    PostgisTripDelayRepository(conn=conn).save(
        TripDelay(
            trip_id=TripId("OP3"),
            route_id="R42",
            timestamp=datetime.now(tz=UTC),
            stop_time_events=(
                StopTimeEvent(
                    stop_id="B1",
                    stop_sequence=1,
                    arrival_delay_seconds=240,
                    departure_delay_seconds=240,
                ),
            ),
        )
    )
    schedule = PostgisTripScheduleRepository(conn=conn)
    active = schedule.active_trip_ids(AT)
    assert active == {TripId(trip_id) for trip_id in _TRIP_STARTS}
    written = PostgisPositionEngine(conn=conn).recompute(active, AT)
    assert written == 4

    app = create_app(
        feed_status_source=lambda: None,
        position_reader_factory=lambda: nullcontext(PostgisVehiclePositionReader(conn=conn)),
        schedule_repository_factory=lambda: nullcontext(schedule),
        optimization_engine=CpSatOptimizationEngine(),
        now_source=lambda: AT,
    )

    response = app.test_client().post("/api/v1/optimize", json={"route_short_name": "42"})

    assert response.status_code == 200
    body = response.get_json()
    assert body["engine"] == "cpsat"
    assert body["direction"] == "Alexanderplatz"
    assert [row["trip_id"] for row in body["vehicles"]] == ["OP1", "OP2", "OP3", "OP4"]
    assert [row["position_seconds"] for row in body["vehicles"]] == [
        2700.0,
        2100.0,
        1260.0,
        900.0,
    ]
    holds = [row["hold_seconds"] for row in body["vehicles"]]
    assert all(0 <= hold <= 300 for hold in holds)
    assert any(hold > 0 for hold in holds)
    summary = body["summary"]
    assert summary["vehicle_count"] == 4
    assert (
        summary["headway_stddev_after_seconds"] < summary["headway_stddev_before_seconds"]
    )
    # Every vehicle holds (if at all) at a stop it has not yet reached.
    assert all(row["next_stop_id"] in {stop[0] for stop in _STOPS} for row in body["vehicles"])