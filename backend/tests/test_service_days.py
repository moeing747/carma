from datetime import date, datetime

from carma.domain.service_days import (
    CalendarException,
    CalendarPeriod,
    ServiceDayInstant,
    resolve_active_services,
    service_day_candidates,
)

# Mon-Fri throughout July 2026.
WEEK = CalendarPeriod(
    service_id="WEEK",
    weekdays=(True, True, True, True, True, False, False),
    start_date=date(2026, 7, 1),
    end_date=date(2026, 7, 31),
)


def test_weekday_inside_range_is_active() -> None:
    # 2026-07-14 is a Tuesday.
    assert resolve_active_services(date(2026, 7, 14), [WEEK], []) == {"WEEK"}


def test_weekend_day_is_inactive() -> None:
    # 2026-07-18 is a Saturday.
    assert resolve_active_services(date(2026, 7, 18), [WEEK], []) == frozenset()


def test_date_outside_range_is_inactive() -> None:
    # A Wednesday, but past end_date.
    assert resolve_active_services(date(2026, 8, 5), [WEEK], []) == frozenset()


def test_removal_exception_wins_over_weekly_pattern() -> None:
    removed = CalendarException(service_id="WEEK", service_date=date(2026, 7, 15), added=False)
    assert resolve_active_services(date(2026, 7, 15), [WEEK], [removed]) == frozenset()


def test_addition_exception_activates_calendar_dates_only_service() -> None:
    added = CalendarException(service_id="SPECIAL", service_date=date(2026, 7, 18), added=True)
    assert resolve_active_services(date(2026, 7, 18), [], [added]) == {"SPECIAL"}


def test_exception_for_other_date_is_ignored() -> None:
    removed = CalendarException(service_id="WEEK", service_date=date(2026, 7, 15), added=False)
    assert resolve_active_services(date(2026, 7, 14), [WEEK], [removed]) == {"WEEK"}


def test_candidates_cover_current_and_previous_service_day() -> None:
    # 00:30 belongs to today's service day at 1800s AND yesterday's at 88200s,
    # so a 25:10:00 stop time on yesterday's trips is still reachable.
    assert service_day_candidates(datetime(2026, 7, 15, 0, 30)) == (
        ServiceDayInstant(date(2026, 7, 15), 1_800),
        ServiceDayInstant(date(2026, 7, 14), 88_200),
    )


def test_candidates_at_midday() -> None:
    assert service_day_candidates(datetime(2026, 7, 14, 12, 0)) == (
        ServiceDayInstant(date(2026, 7, 14), 43_200),
        ServiceDayInstant(date(2026, 7, 13), 129_600),
    )
