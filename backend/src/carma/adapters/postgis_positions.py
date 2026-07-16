"""Set-based PostGIS position engine and vehicle_positions reader.

The engine implements the semantics defined in carma.domain.positioning as
one SQL statement over all active trips per tick — no per-trip Python loop.
The equivalence test holds its output against the pure reference
implementation on the fixture feed.

The engine positions a trip only while its *scheduled* span covers the
instant — the contract it shares with the Phase 1 active-trip filter that
selects its input. Consequence, accepted and visible on the map: a delayed
trip disappears at its scheduled end even when its effective (delayed) end
lies later. Fixing that would mean delay-aware activity resolution, a
schedule-repository concern, not a projection one.

Stop-to-shape fractions (where each stop sits along its trip's shape) are
computed with ST_LineLocatePoint because VBB's shape_dist_traveled is
unreliable/absent, and cached in shape_stop_fractions: locating all ~254k
distinct (shape, stop) pairs of the full feed costs ~40s of PostGIS time,
far too much per tick. The cache fills lazily for the shapes referenced by
the trips of each tick; once warm, the fill is a no-op anti-join.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg

from carma.adapters.postgis_schedule import to_feed_local
from carma.domain.models import BoundingBox, TripId, VehiclePosition
from carma.domain.service_days import service_day_candidates

_ENSURE_FRACTIONS_SQL = """
INSERT INTO shape_stop_fractions (shape_id, stop_id, fraction)
SELECT p.shape_id, p.stop_id, ST_LineLocatePoint(sh.geom, s.geom)
FROM (
    SELECT DISTINCT t.shape_id, st.stop_id
    FROM trips t
    JOIN stop_times st ON st.trip_id = t.trip_id
    WHERE t.trip_id = ANY(%(trip_ids)s) AND t.shape_id IS NOT NULL
) p
JOIN shapes sh ON sh.shape_id = p.shape_id
JOIN stops s ON s.stop_id = p.stop_id AND s.geom IS NOT NULL
WHERE NOT EXISTS (
    SELECT 1 FROM shape_stop_fractions f
    WHERE f.shape_id = p.shape_id AND f.stop_id = p.stop_id
)
ON CONFLICT DO NOTHING
"""

# Each CTE mirrors a step of carma.domain.positioning; the comments name the
# counterpart so the two implementations can be audited side by side.
_RECOMPUTE_SQL = """
WITH active AS (
    -- Which service-day instant applies to each trip: today's seconds (s1)
    -- when the scheduled span covers them, else yesterday's (s2 = s1+86400,
    -- the midnight-crossing candidate from carma.domain.service_days).
    SELECT t.trip_id, t.route_id, t.shape_id,
           CASE WHEN t.first_departure_seconds <= %(s1)s AND t.last_arrival_seconds >= %(s1)s
                THEN %(s1)s ELSE %(s2)s END AS t_now
    FROM trips t
    WHERE t.trip_id = ANY(%(trip_ids)s)
      AND ((t.first_departure_seconds <= %(s1)s AND t.last_arrival_seconds >= %(s1)s)
        OR (t.first_departure_seconds <= %(s2)s AND t.last_arrival_seconds >= %(s2)s))
),
timed AS (
    -- positioning: stops without any time (or coordinates) carry nothing.
    SELECT a.trip_id, a.route_id, a.shape_id, a.t_now,
           st.stop_sequence, st.stop_id, s.geom AS stop_geom,
           COALESCE(st.arrival_seconds, st.departure_seconds) AS sched_arr,
           COALESCE(st.departure_seconds, st.arrival_seconds) AS sched_dep
    FROM active a
    JOIN stop_times st ON st.trip_id = a.trip_id
    JOIN stops s ON s.stop_id = st.stop_id AND s.geom IS NOT NULL
    WHERE COALESCE(st.arrival_seconds, st.departure_seconds) IS NOT NULL
),
events AS (
    SELECT td.trip_id,
           (e.value ->> 'stop_sequence')::int           AS event_sequence,
           (e.value ->> 'arrival_delay_seconds')::int   AS arrival_delay,
           (e.value ->> 'departure_delay_seconds')::int AS departure_delay
    FROM trip_delays td
    CROSS JOIN LATERAL jsonb_array_elements(td.stop_time_events) AS e(value)
    WHERE td.trip_id = ANY(%(trip_ids)s)
),
applicable AS (
    -- positioning._adjustments: the latest event at or before each stop.
    SELECT DISTINCT ON (tm.trip_id, tm.stop_sequence)
           tm.trip_id, tm.stop_sequence,
           ev.event_sequence, ev.arrival_delay, ev.departure_delay
    FROM timed tm
    JOIN events ev ON ev.trip_id = tm.trip_id AND ev.event_sequence <= tm.stop_sequence
    ORDER BY tm.trip_id, tm.stop_sequence, ev.event_sequence DESC
),
adjusted AS (
    -- The event's own stop takes its arrival delay; later stops take the
    -- propagated value (departure delay, falling back to arrival).
    SELECT tm.*,
           tm.sched_arr + CASE
               WHEN ap.event_sequence IS NULL THEN 0
               WHEN ap.event_sequence = tm.stop_sequence
                   THEN COALESCE(ap.arrival_delay, ap.departure_delay, 0)
               ELSE COALESCE(ap.departure_delay, ap.arrival_delay, 0)
           END AS eff_arr,
           tm.sched_dep + CASE
               WHEN ap.event_sequence IS NULL THEN 0
               ELSE COALESCE(ap.departure_delay, ap.arrival_delay, 0)
           END AS eff_dep
    FROM timed tm
    LEFT JOIN applicable ap
        ON ap.trip_id = tm.trip_id AND ap.stop_sequence = tm.stop_sequence
),
clamped AS (
    -- positioning.effective_stop_times: times never decrease in stop order
    -- (clamped_dep(i) = max over j<=i of max(eff_arr(j), eff_dep(j))).
    SELECT a.*,
           GREATEST(a.eff_arr,
                    COALESCE(MAX(GREATEST(a.eff_arr, a.eff_dep)) OVER prior, a.eff_arr)) AS c_arr
    FROM adjusted a
    WINDOW prior AS (PARTITION BY a.trip_id ORDER BY a.stop_sequence
                     ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
),
fractions AS (
    -- positioning._monotonic_shape_fractions over the cached fractions.
    SELECT c.*,
           GREATEST(c.eff_dep, c.c_arr) AS c_dep,
           MAX(f.fraction) OVER (PARTITION BY c.trip_id ORDER BY c.stop_sequence
                                 ROWS UNBOUNDED PRECEDING) AS stop_fraction
    FROM clamped c
    LEFT JOIN shape_stop_fractions f
        ON f.shape_id = c.shape_id AND f.stop_id = c.stop_id
),
segments AS (
    SELECT f.*,
           ROW_NUMBER() OVER w          AS row_index,
           LEAD(f.c_arr) OVER w         AS next_arr,
           LEAD(f.sched_arr) OVER w     AS next_sched_arr,
           LEAD(f.stop_geom) OVER w     AS next_geom,
           LEAD(f.stop_fraction) OVER w AS next_fraction
    FROM fractions f
    WINDOW w AS (PARTITION BY f.trip_id ORDER BY f.stop_sequence)
),
located AS (
    -- positioning._progress: parked before the first arrival, pinned while
    -- dwelling (segment_fraction NULL), else travelling on the segment.
    -- Trips past their last effective arrival match no row and drop out.
    SELECT DISTINCT ON (s.trip_id) s.*,
           CASE WHEN (s.row_index = 1 AND s.t_now < s.c_arr)
                  OR (s.c_arr <= s.t_now AND s.t_now <= s.c_dep)
                THEN NULL
                ELSE (s.t_now - s.c_dep)::double precision / (s.next_arr - s.c_dep)
           END AS segment_fraction
    FROM segments s
    WHERE (s.row_index = 1 AND s.t_now < s.c_arr)
       OR (s.c_arr <= s.t_now AND s.t_now <= s.c_dep)
       OR (s.c_dep < s.t_now AND s.next_arr IS NOT NULL AND s.t_now < s.next_arr)
    ORDER BY s.trip_id, s.stop_sequence
),
projected AS (
    -- positioning.derive_position: shape when the fractions are cached,
    -- straight stop-to-stop line otherwise; the reported delay comes from
    -- the clamped times (next arrival while travelling, own departure
    -- while pinned).
    SELECT l.trip_id, l.route_id, l.stop_geom,
           CASE WHEN l.segment_fraction IS NULL
                THEN l.c_dep - l.sched_dep
                ELSE l.next_arr - l.next_sched_arr
           END AS delay_seconds,
           CASE
               WHEN sh.geom IS NOT NULL AND l.stop_fraction IS NOT NULL
                    AND (l.segment_fraction IS NULL OR l.next_fraction IS NOT NULL)
                   THEN sh.geom
               WHEN l.segment_fraction IS NOT NULL
                   THEN ST_MakeLine(l.stop_geom, l.next_geom)
           END AS line_geom,
           CASE
               WHEN sh.geom IS NOT NULL AND l.stop_fraction IS NOT NULL
                    AND l.segment_fraction IS NULL
                   THEN l.stop_fraction
               WHEN sh.geom IS NOT NULL AND l.stop_fraction IS NOT NULL
                    AND l.next_fraction IS NOT NULL
                   THEN l.stop_fraction
                        + l.segment_fraction * (l.next_fraction - l.stop_fraction)
               WHEN l.segment_fraction IS NOT NULL
                   THEN l.segment_fraction
           END AS line_fraction
    FROM located l
    LEFT JOIN shapes sh ON sh.shape_id = l.shape_id
),
computed AS (
    -- geometry.point_at_fraction / bearing_at_fraction: the bearing samples
    -- ST_Azimuth over the same forward epsilon as the pure reference.
    SELECT q.trip_id, q.route_id, q.delay_seconds,
           CASE WHEN q.line_geom IS NULL OR q.frac IS NULL THEN q.stop_geom
                ELSE ST_LineInterpolatePoint(q.line_geom, q.frac)
           END AS geom,
           CASE WHEN q.line_geom IS NULL OR q.frac IS NULL THEN NULL
                ELSE degrees(ST_Azimuth(
                        ST_LineInterpolatePoint(q.line_geom, q.frac_start)::geography,
                        ST_LineInterpolatePoint(q.line_geom, q.frac_start + 0.001)::geography))
           END AS bearing
    FROM (
        SELECT p.*,
               LEAST(GREATEST(p.line_fraction, 0.0), 1.0) AS frac,
               GREATEST(LEAST(LEAST(GREATEST(p.line_fraction, 0.0), 1.0), 0.999), 0.0)
                   AS frac_start
        FROM projected p
    ) q
),
removed AS (
    DELETE FROM vehicle_positions vp
    WHERE NOT EXISTS (SELECT 1 FROM computed c WHERE c.trip_id = vp.trip_id)
    RETURNING vp.trip_id
),
upserted AS (
    INSERT INTO vehicle_positions AS v
        (trip_id, route_id, geom, bearing, delay_seconds, computed_at)
    SELECT c.trip_id, c.route_id, c.geom, c.bearing, c.delay_seconds, now()
    FROM computed c
    ON CONFLICT (trip_id) DO UPDATE SET
        route_id = EXCLUDED.route_id,
        geom = EXCLUDED.geom,
        bearing = EXCLUDED.bearing,
        delay_seconds = EXCLUDED.delay_seconds,
        computed_at = EXCLUDED.computed_at
    RETURNING v.trip_id
)
SELECT (SELECT count(*) FROM upserted) AS written,
       (SELECT count(*) FROM removed) AS removed
"""

# route_short_name and headsign are joined from the static tables at read
# time rather than denormalized into vehicle_positions: the projection stays
# minimal/rebuildable, and both joins are primary-key lookups.
_POSITION_COLUMNS = """
SELECT vp.trip_id, vp.route_id, COALESCE(r.route_short_name, '') AS route_short_name,
       ST_Y(vp.geom) AS lat, ST_X(vp.geom) AS lon,
       vp.bearing, vp.delay_seconds, vp.computed_at,
       COALESCE(t.trip_headsign, '') AS headsign
FROM vehicle_positions vp
LEFT JOIN routes r ON r.route_id = vp.route_id
LEFT JOIN trips t ON t.trip_id = vp.trip_id
"""


@dataclass(frozen=True, slots=True)
class PostgisPositionEngine:
    """PositionRecomputeEngine over the GTFS + trip_delays tables.

    Pass a connection with autocommit=True: each tick runs in one explicit
    transaction, so readers never observe a half-recomputed table.
    """

    conn: psycopg.Connection[Any]

    def recompute(self, trip_ids: Collection[TripId], at: datetime) -> int:
        ids = sorted(trip_id.value for trip_id in trip_ids)
        today, yesterday = service_day_candidates(to_feed_local(self.conn, at))
        with self.conn.transaction():
            self.conn.execute(_ENSURE_FRACTIONS_SQL, {"trip_ids": ids})
            row = self.conn.execute(
                _RECOMPUTE_SQL,
                {
                    "trip_ids": ids,
                    "s1": today.seconds_into_day,
                    "s2": yesterday.seconds_into_day,
                },
            ).fetchone()
        if row is None:  # pragma: no cover - the statement always returns one row
            raise RuntimeError("recompute statement returned no counts row")
        return int(row[0])


@dataclass(frozen=True, slots=True)
class PostgisVehiclePositionReader:
    conn: psycopg.Connection[Any]

    def positions(self, bbox: BoundingBox | None, limit: int) -> tuple[VehiclePosition, ...]:
        if bbox is None:
            rows = self.conn.execute(
                _POSITION_COLUMNS + " ORDER BY vp.trip_id LIMIT %(limit)s",
                {"limit": limit},
            ).fetchall()
        else:
            rows = self.conn.execute(
                _POSITION_COLUMNS
                + " WHERE vp.geom && ST_MakeEnvelope("
                "%(min_lon)s, %(min_lat)s, %(max_lon)s, %(max_lat)s, 4326)"
                " ORDER BY vp.trip_id LIMIT %(limit)s",
                {
                    "min_lon": bbox.min_lon,
                    "min_lat": bbox.min_lat,
                    "max_lon": bbox.max_lon,
                    "max_lat": bbox.max_lat,
                    "limit": limit,
                },
            ).fetchall()
        return tuple(_position_from_row(row) for row in rows)

    def positions_since(
        self, cursor: datetime | None, limit: int
    ) -> tuple[VehiclePosition, ...]:
        if cursor is None:
            rows = self.conn.execute(
                _POSITION_COLUMNS + " ORDER BY vp.computed_at, vp.trip_id LIMIT %(limit)s",
                {"limit": limit},
            ).fetchall()
        else:
            rows = self.conn.execute(
                _POSITION_COLUMNS
                + " WHERE vp.computed_at > %(cursor)s"
                " ORDER BY vp.computed_at, vp.trip_id LIMIT %(limit)s",
                {"cursor": cursor, "limit": limit},
            ).fetchall()
        return tuple(_position_from_row(row) for row in rows)

    def position_for_trip(self, trip_id: TripId) -> VehiclePosition | None:
        row = self.conn.execute(
            _POSITION_COLUMNS + " WHERE vp.trip_id = %(trip_id)s",
            {"trip_id": trip_id.value},
        ).fetchone()
        return None if row is None else _position_from_row(row)


def _position_from_row(row: tuple[Any, ...]) -> VehiclePosition:
    (
        trip_id,
        route_id,
        route_short_name,
        lat,
        lon,
        bearing,
        delay_seconds,
        computed_at,
        headsign,
    ) = row
    return VehiclePosition(
        trip_id=TripId(trip_id),
        route_id=route_id,
        route_short_name=route_short_name,
        lat=lat,
        lon=lon,
        bearing=bearing,
        delay_seconds=delay_seconds,
        computed_at=computed_at,
        headsign=headsign,
    )
