from datetime import date

import pytest

from carma.adapters.gtfs_static import parse_gtfs_date, parse_gtfs_time


def test_parses_ordinary_time() -> None:
    assert parse_gtfs_time("08:05:30") == 8 * 3600 + 5 * 60 + 30


def test_parses_time_past_midnight() -> None:
    assert parse_gtfs_time("25:10:00") == 25 * 3600 + 10 * 60


def test_parses_single_digit_hour() -> None:
    assert parse_gtfs_time("7:05:00") == 7 * 3600 + 5 * 60


def test_blank_time_is_none() -> None:
    assert parse_gtfs_time("") is None
    assert parse_gtfs_time("   ") is None


@pytest.mark.parametrize("value", ["08:05", "8h05m00s", "08:61:00", "08:00:61", "-1:00:00"])
def test_malformed_time_raises(value: str) -> None:
    with pytest.raises(ValueError, match="GTFS time"):
        parse_gtfs_time(value)


def test_parses_date() -> None:
    assert parse_gtfs_date("20260715") == date(2026, 7, 15)


def test_malformed_date_raises() -> None:
    with pytest.raises(ValueError, match="GTFS date"):
        parse_gtfs_date("2026-07-15")
