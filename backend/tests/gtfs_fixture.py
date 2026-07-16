"""Handcrafted mini GTFS feed for loader and repository tests.

Layout: 2 routes, 3 trips, 6 stops, 1 shape, one weekly calendar service
(WEEK, Mon-Fri July 2026) with a removal exception on 2026-07-15, plus a
calendar_dates-only service (SPECIAL) added on Saturday 2026-07-18. Trip T2
crosses midnight (23:50:00 -> 25:10:00).
"""

import csv
import io
import zipfile
from pathlib import Path

_FILES: dict[str, list[list[str]]] = {
    "agency.txt": [
        ["agency_id", "agency_name", "agency_url", "agency_timezone"],
        ["A1", "Mini Verkehr", "https://example.org", "Europe/Berlin"],
    ],
    "routes.txt": [
        ["route_id", "agency_id", "route_short_name", "route_long_name", "route_type"],
        ["R1", "A1", "M1", "Mitte - Nord", "3"],
        ["R2", "A1", "T2", "Ring", "0"],
    ],
    "stops.txt": [
        ["stop_id", "stop_name", "stop_lat", "stop_lon"],
        ["S1", "Alexanderplatz", "52.5219", "13.4132"],
        ["S2", "Hackescher Markt", "52.5225", "13.4021"],
        ["S3", "Friedrichstr.", "52.5203", "13.3872"],
        ["S4", "Hauptbahnhof", "52.5250", "13.3694"],
        ["S5", "Bellevue", "52.5199", "13.3470"],
        ["S6", "Tiergarten", "52.5142", "13.3364"],
    ],
    "trips.txt": [
        ["trip_id", "route_id", "service_id", "trip_headsign", "direction_id", "shape_id"],
        ["T1", "R1", "WEEK", "Hauptbahnhof", "0", "SH1"],
        ["T2", "R1", "WEEK", "Tiergarten", "1", ""],
        ["T3", "R2", "SPECIAL", "Ring", "0", ""],
    ],
    "stop_times.txt": [
        ["trip_id", "stop_sequence", "stop_id", "arrival_time", "departure_time",
         "shape_dist_traveled"],
        ["T1", "1", "S1", "08:00:00", "08:00:00", "0"],
        # Empty shape_dist_traveled is common in real feeds.
        ["T1", "2", "S2", "08:10:00", "08:11:00", ""],
        ["T1", "3", "S3", "08:20:00", "08:21:00", ""],
        ["T1", "4", "S4", "08:30:00", "08:30:00", "3200.5"],
        # T2 crosses midnight: GTFS times exceed 24:00:00.
        ["T2", "1", "S4", "23:50:00", "23:50:00", ""],
        ["T2", "2", "S5", "24:30:00", "24:31:00", ""],
        ["T2", "3", "S6", "25:10:00", "25:10:00", ""],
        ["T3", "1", "S1", "10:00:00", "10:00:00", ""],
        ["T3", "2", "S5", "10:10:00", "10:11:00", ""],
        ["T3", "3", "S6", "10:20:00", "10:20:00", ""],
    ],
    "shapes.txt": [
        ["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence", "shape_dist_traveled"],
        ["SH1", "52.5219", "13.4132", "1", "0"],
        ["SH1", "52.5225", "13.4021", "2", ""],
        ["SH1", "52.5203", "13.3872", "3", ""],
        ["SH1", "52.5230", "13.3780", "4", ""],
        ["SH1", "52.5250", "13.3694", "5", ""],
    ],
    "calendar.txt": [
        ["service_id", "monday", "tuesday", "wednesday", "thursday", "friday",
         "saturday", "sunday", "start_date", "end_date"],
        ["WEEK", "1", "1", "1", "1", "1", "0", "0", "20260701", "20260731"],
    ],
    "calendar_dates.txt": [
        ["service_id", "date", "exception_type"],
        # WEEK removed on Wednesday 2026-07-15; SPECIAL (no calendar.txt row,
        # the VBB-style calendar_dates-only case) added on Saturday 2026-07-18.
        ["WEEK", "20260715", "2"],
        ["SPECIAL", "20260718", "1"],
    ],
}

EXPECTED_ROW_COUNTS = {
    "agencies": 1,
    "routes": 2,
    "trips": 3,
    "stops": 6,
    "stop_times": 10,
    "shapes": 1,
    "calendar": 1,
    "calendar_dates": 2,
}


def write_fixture_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, rows in _FILES.items():
            buffer = io.StringIO()
            csv.writer(buffer, lineterminator="\n").writerows(rows)
            content = buffer.getvalue()
            # Real feeds frequently carry a UTF-8 BOM; exercise that path.
            if name == "stops.txt":
                content = "\ufeff" + content
            archive.writestr(name, content)
    return path
