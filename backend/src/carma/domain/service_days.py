"""Service-day resolution for GTFS calendars.

A GTFS trip belongs to a *service day*, and its stop times may run past
24:00:00 — a 25:10:00 arrival happens at 01:10 wall clock on the next
calendar date but still belongs to the previous service day. Carma's
convention: an instant maps to two candidate service days, the current
calendar date (seconds since its local midnight) and the previous one (the
same seconds + 86400). A trip is active at the instant when its service runs
on the candidate day and its stop_times span covers the candidate's
seconds-into-day value. One day of lookback covers stop times up to 48h,
beyond anything VBB publishes. Seconds count from local midnight rather than
the GTFS "noon minus 12h" anchor, so results can be off by one hour during
the two DST transition nights per year — accepted for this project.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

SECONDS_PER_DAY = 86_400


@dataclass(frozen=True, slots=True)
class CalendarPeriod:
    """One calendar.txt row: a weekly pattern valid over a date range."""

    service_id: str
    # Monday-first, matching date.weekday().
    weekdays: tuple[bool, bool, bool, bool, bool, bool, bool]
    start_date: date
    end_date: date


@dataclass(frozen=True, slots=True)
class CalendarException:
    """One calendar_dates.txt row: service added to or removed from a date."""

    service_id: str
    service_date: date
    added: bool


@dataclass(frozen=True, slots=True)
class ServiceDayInstant:
    service_date: date
    seconds_into_day: int


def service_day_candidates(local_at: datetime) -> tuple[ServiceDayInstant, ...]:
    """Map a feed-local wall-clock instant to its candidate service days."""
    midnight = local_at.replace(hour=0, minute=0, second=0, microsecond=0)
    seconds = int((local_at - midnight).total_seconds())
    return (
        ServiceDayInstant(local_at.date(), seconds),
        ServiceDayInstant(local_at.date() - timedelta(days=1), seconds + SECONDS_PER_DAY),
    )


def resolve_active_services(
    on: date,
    periods: Iterable[CalendarPeriod],
    exceptions: Iterable[CalendarException],
) -> frozenset[str]:
    """Services running on a date: weekly pattern plus calendar_dates overrides.

    Works for calendar-only feeds (no exceptions), calendar_dates-only feeds
    (no periods; VBB is largely this), and mixed ones.
    """
    active = {
        period.service_id
        for period in periods
        if period.start_date <= on <= period.end_date and period.weekdays[on.weekday()]
    }
    for exception in exceptions:
        if exception.service_date != on:
            continue
        if exception.added:
            active.add(exception.service_id)
        else:
            active.discard(exception.service_id)
    return frozenset(active)
