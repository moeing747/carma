from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

from carma.domain.models import Coordinate, ScheduledStop, TripId
from carma.domain.service_days import (
    CalendarException,
    CalendarPeriod,
    resolve_active_services,
    service_day_candidates,
)

_FALLBACK_TIMEZONE = "Europe/Berlin"


@dataclass(frozen=True, slots=True)
class PostgisTripScheduleRepository:
    """TripScheduleRepository implementation over the loaded GTFS tables."""

    conn: psycopg.Connection[Any]

    def active_trip_ids(self, at: datetime) -> frozenset[TripId]:
        local = self._to_feed_local(at)
        periods = self._calendar_periods()
        active: set[TripId] = set()
        for instant in service_day_candidates(local):
            services = resolve_active_services(
                instant.service_date, periods, self._exceptions_on(instant.service_date)
            )
            if not services:
                continue
            rows = self.conn.execute(
                "SELECT trip_id FROM trips"
                " WHERE service_id = ANY(%s)"
                " AND first_departure_seconds <= %s AND last_arrival_seconds >= %s",
                (sorted(services), instant.seconds_into_day, instant.seconds_into_day),
            ).fetchall()
            active.update(TripId(row[0]) for row in rows)
        return frozenset(active)

    def schedule_for_trip(self, trip_id: TripId) -> tuple[ScheduledStop, ...]:
        rows = self.conn.execute(
            "SELECT st.stop_id, s.stop_name, st.stop_sequence,"
            "       st.arrival_seconds, st.departure_seconds, ST_Y(s.geom), ST_X(s.geom)"
            " FROM stop_times st JOIN stops s ON s.stop_id = st.stop_id"
            " WHERE st.trip_id = %s ORDER BY st.stop_sequence",
            (trip_id.value,),
        ).fetchall()
        schedule = []
        for stop_id, stop_name, sequence, arrival, departure, lat, lon in rows:
            if lat is None or lon is None:
                raise ValueError(f"stop {stop_id} on trip {trip_id.value} has no coordinates")
            schedule.append(
                ScheduledStop(
                    stop_id=stop_id,
                    stop_name=stop_name,
                    stop_sequence=sequence,
                    arrival_seconds=arrival,
                    departure_seconds=departure,
                    coordinate=Coordinate(lat=lat, lon=lon),
                )
            )
        return tuple(schedule)

    def shape_for_trip(self, trip_id: TripId) -> tuple[Coordinate, ...] | None:
        rows = self.conn.execute(
            "SELECT ST_Y((d).geom), ST_X((d).geom)"
            " FROM trips t"
            " JOIN shapes sh ON sh.shape_id = t.shape_id"
            " CROSS JOIN LATERAL ST_DumpPoints(sh.geom) AS d"
            " WHERE t.trip_id = %s"
            " ORDER BY (d).path[1]",
            (trip_id.value,),
        ).fetchall()
        if not rows:
            return None
        return tuple(Coordinate(lat=lat, lon=lon) for lat, lon in rows)

    def _to_feed_local(self, at: datetime) -> datetime:
        """Naive feed-local wall time; aware inputs convert via agency timezone."""
        if at.tzinfo is None:
            return at
        row = self.conn.execute("SELECT agency_timezone FROM agencies LIMIT 1").fetchone()
        timezone = row[0] if row and row[0] else _FALLBACK_TIMEZONE
        return at.astimezone(ZoneInfo(timezone)).replace(tzinfo=None)

    def _calendar_periods(self) -> tuple[CalendarPeriod, ...]:
        rows = self.conn.execute(
            "SELECT service_id, monday, tuesday, wednesday, thursday, friday,"
            " saturday, sunday, start_date, end_date FROM calendar"
        ).fetchall()
        return tuple(
            CalendarPeriod(
                service_id=row[0],
                weekdays=(row[1], row[2], row[3], row[4], row[5], row[6], row[7]),
                start_date=row[8],
                end_date=row[9],
            )
            for row in rows
        )

    def _exceptions_on(self, service_date: date) -> tuple[CalendarException, ...]:
        rows = self.conn.execute(
            "SELECT service_id, exception_type FROM calendar_dates WHERE service_date = %s",
            (service_date,),
        ).fetchall()
        return tuple(
            CalendarException(
                service_id=row[0], service_date=service_date, added=row[1] == 1
            )
            for row in rows
        )
