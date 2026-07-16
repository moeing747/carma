"""Static GTFS zip loader: streams CSVs out of the archive into PostGIS.

Bulk loads via COPY (VBB's stop_times has millions of rows) inside one
transaction with truncate-and-reload semantics — honest for a snapshot
dataset and idempotent by construction.
"""

import csv
import io
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import psycopg

_REQUIRED_FILES = ("routes.txt", "trips.txt", "stops.txt", "stop_times.txt")


def parse_gtfs_time(value: str) -> int | None:
    """GTFS ``HH:MM:SS`` to seconds since service-day start; blank -> None.

    Hours may exceed 23 (a 25:10:00 stop happens at 01:10 the next calendar
    day but belongs to the previous service day) and may be single-digit.
    """
    text = value.strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) != 3:
        raise ValueError(f"malformed GTFS time: {value!r}")
    hours, minutes, seconds = (int(part) for part in parts)
    if not (0 <= minutes < 60 and 0 <= seconds < 60 and hours >= 0):
        raise ValueError(f"malformed GTFS time: {value!r}")
    return hours * 3600 + minutes * 60 + seconds


def parse_gtfs_date(value: str) -> date:
    """GTFS ``YYYYMMDD`` to a date."""
    text = value.strip()
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"malformed GTFS date: {value!r}")
    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))


@dataclass(frozen=True, slots=True)
class LoadReport:
    rows_loaded: dict[str, int]


def load_gtfs_zip(conn: psycopg.Connection[Any], zip_path: Path) -> LoadReport:
    """Full reload of the static GTFS tables from a feed zip, atomically."""
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        missing = [name for name in _REQUIRED_FILES if _member(names, name) is None]
        if missing:
            raise ValueError(f"GTFS zip is missing required files: {', '.join(missing)}")
        if _member(names, "calendar.txt") is None and _member(names, "calendar_dates.txt") is None:
            raise ValueError("GTFS zip has neither calendar.txt nor calendar_dates.txt")
        with conn.transaction():
            conn.execute(
                "TRUNCATE agencies, routes, trips, stops, stop_times,"
                " shapes, shape_points, calendar, calendar_dates"
            )
            counts = {
                "agencies": _load_agencies(conn, archive),
                "routes": _load_routes(conn, archive),
                "trips": _load_trips(conn, archive),
                "stops": _load_stops(conn, archive),
                "stop_times": _load_stop_times(conn, archive),
                "shapes": _load_shapes(conn, archive),
                "calendar": _load_calendar(conn, archive),
                "calendar_dates": _load_calendar_dates(conn, archive),
            }
            _denormalize_trip_spans(conn)
    return LoadReport(rows_loaded=counts)


def _member(names: set[str], filename: str) -> str | None:
    # Some feeds nest the files inside a directory in the archive.
    if filename in names:
        return filename
    return next((name for name in names if name.endswith("/" + filename)), None)


def _rows(archive: zipfile.ZipFile, filename: str) -> Iterator[dict[str, str]]:
    member = _member(set(archive.namelist()), filename)
    if member is None:
        return
    with archive.open(member) as raw:
        # utf-8-sig: GTFS files frequently start with a BOM.
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        yield from csv.DictReader(text)


def _field(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    return value.strip() if isinstance(value, str) else ""


def _optional_int(text: str) -> int | None:
    return int(text) if text else None


def _optional_float(text: str) -> float | None:
    return float(text) if text else None


def _load_agencies(conn: psycopg.Connection[Any], archive: zipfile.ZipFile) -> int:
    count = 0
    with (
        conn.cursor() as cursor,
        cursor.copy(
            "COPY agencies (agency_id, agency_name, agency_url, agency_timezone) FROM STDIN"
        ) as copy,
    ):
        for row in _rows(archive, "agency.txt"):
            # agency_id is optional when the feed has a single agency.
            copy.write_row(
                (
                    _field(row, "agency_id"),
                    _field(row, "agency_name"),
                    _field(row, "agency_url"),
                    _field(row, "agency_timezone"),
                )
            )
            count += 1
    return count


def _load_routes(conn: psycopg.Connection[Any], archive: zipfile.ZipFile) -> int:
    count = 0
    with (
        conn.cursor() as cursor,
        cursor.copy(
            "COPY routes (route_id, agency_id, route_short_name, route_long_name, route_type)"
            " FROM STDIN"
        ) as copy,
    ):
        for row in _rows(archive, "routes.txt"):
            copy.write_row(
                (
                    _field(row, "route_id"),
                    _field(row, "agency_id"),
                    _field(row, "route_short_name"),
                    _field(row, "route_long_name"),
                    int(_field(row, "route_type")),
                )
            )
            count += 1
    return count


def _load_trips(conn: psycopg.Connection[Any], archive: zipfile.ZipFile) -> int:
    count = 0
    with (
        conn.cursor() as cursor,
        cursor.copy(
            "COPY trips (trip_id, route_id, service_id, trip_headsign, direction_id, shape_id)"
            " FROM STDIN"
        ) as copy,
    ):
        for row in _rows(archive, "trips.txt"):
            copy.write_row(
                (
                    _field(row, "trip_id"),
                    _field(row, "route_id"),
                    _field(row, "service_id"),
                    _field(row, "trip_headsign"),
                    _optional_int(_field(row, "direction_id")),
                    _field(row, "shape_id") or None,
                )
            )
            count += 1
    return count


def _load_stops(conn: psycopg.Connection[Any], archive: zipfile.ZipFile) -> int:
    count = 0
    with (
        conn.cursor() as cursor,
        cursor.copy(
            "COPY stops (stop_id, stop_name, parent_station, location_type, geom) FROM STDIN"
        ) as copy,
    ):
        for row in _rows(archive, "stops.txt"):
            lat, lon = _field(row, "stop_lat"), _field(row, "stop_lon")
            # Coordinates may legitimately be blank for generic nodes.
            geom = f"SRID=4326;POINT({float(lon)} {float(lat)})" if lat and lon else None
            copy.write_row(
                (
                    _field(row, "stop_id"),
                    _field(row, "stop_name"),
                    _field(row, "parent_station") or None,
                    _optional_int(_field(row, "location_type")),
                    geom,
                )
            )
            count += 1
    return count


def _load_stop_times(conn: psycopg.Connection[Any], archive: zipfile.ZipFile) -> int:
    count = 0
    with (
        conn.cursor() as cursor,
        cursor.copy(
            "COPY stop_times (trip_id, stop_sequence, stop_id, arrival_seconds,"
            " departure_seconds, shape_dist_traveled) FROM STDIN"
        ) as copy,
    ):
        for row in _rows(archive, "stop_times.txt"):
            copy.write_row(
                (
                    _field(row, "trip_id"),
                    int(_field(row, "stop_sequence")),
                    _field(row, "stop_id"),
                    parse_gtfs_time(_field(row, "arrival_time")),
                    parse_gtfs_time(_field(row, "departure_time")),
                    _optional_float(_field(row, "shape_dist_traveled")),
                )
            )
            count += 1
    return count


def _load_shapes(conn: psycopg.Connection[Any], archive: zipfile.ZipFile) -> int:
    with (
        conn.cursor() as cursor,
        cursor.copy("COPY shape_points (shape_id, shape_pt_sequence, lat, lon) FROM STDIN") as copy,
    ):
        for row in _rows(archive, "shapes.txt"):
            copy.write_row(
                (
                    _field(row, "shape_id"),
                    int(_field(row, "shape_pt_sequence")),
                    float(_field(row, "shape_pt_lat")),
                    float(_field(row, "shape_pt_lon")),
                )
            )
    result = conn.execute(
        "INSERT INTO shapes (shape_id, geom)"
        " SELECT shape_id,"
        "        ST_MakeLine(ST_SetSRID(ST_MakePoint(lon, lat), 4326) ORDER BY shape_pt_sequence)"
        " FROM shape_points GROUP BY shape_id HAVING count(*) >= 2"
    )
    conn.execute("TRUNCATE shape_points")
    return result.rowcount


def _load_calendar(conn: psycopg.Connection[Any], archive: zipfile.ZipFile) -> int:
    count = 0
    with (
        conn.cursor() as cursor,
        cursor.copy(
            "COPY calendar (service_id, monday, tuesday, wednesday, thursday, friday,"
            " saturday, sunday, start_date, end_date) FROM STDIN"
        ) as copy,
    ):
        for row in _rows(archive, "calendar.txt"):
            weekdays = tuple(
                _field(row, day) == "1"
                for day in (
                    "monday",
                    "tuesday",
                    "wednesday",
                    "thursday",
                    "friday",
                    "saturday",
                    "sunday",
                )
            )
            copy.write_row(
                (
                    _field(row, "service_id"),
                    *weekdays,
                    parse_gtfs_date(_field(row, "start_date")),
                    parse_gtfs_date(_field(row, "end_date")),
                )
            )
            count += 1
    return count


def _load_calendar_dates(conn: psycopg.Connection[Any], archive: zipfile.ZipFile) -> int:
    count = 0
    with (
        conn.cursor() as cursor,
        cursor.copy(
            "COPY calendar_dates (service_id, service_date, exception_type) FROM STDIN"
        ) as copy,
    ):
        for row in _rows(archive, "calendar_dates.txt"):
            exception_type = int(_field(row, "exception_type"))
            if exception_type not in (1, 2):
                raise ValueError(f"unknown calendar_dates exception_type: {exception_type}")
            copy.write_row(
                (
                    _field(row, "service_id"),
                    parse_gtfs_date(_field(row, "date")),
                    exception_type,
                )
            )
            count += 1
    return count


def _denormalize_trip_spans(conn: psycopg.Connection[Any]) -> None:
    conn.execute(
        "UPDATE trips t"
        " SET first_departure_seconds = s.first_departure, last_arrival_seconds = s.last_arrival"
        " FROM (SELECT trip_id,"
        "              MIN(COALESCE(departure_seconds, arrival_seconds)) AS first_departure,"
        "              MAX(COALESCE(arrival_seconds, departure_seconds)) AS last_arrival"
        "       FROM stop_times GROUP BY trip_id) s"
        " WHERE t.trip_id = s.trip_id"
    )
