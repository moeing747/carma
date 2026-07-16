-- 0003: derived vehicle positions (a projection, never a source of truth).
--
-- vehicle_positions is UNLOGGED deliberately: every row is recomputed from
-- (static schedule + trip_delays) every few seconds, so crash durability
-- would buy nothing — after a restart the next projection tick rebuilds the
-- table from scratch. State here is a projection, and projections are
-- rebuildable by definition.

CREATE UNLOGGED TABLE vehicle_positions (
    trip_id       TEXT PRIMARY KEY,
    route_id      TEXT NOT NULL,
    geom          geometry(Point, 4326) NOT NULL,
    bearing       DOUBLE PRECISION,   -- NULL: standing still off-shape, no direction
    delay_seconds INTEGER NOT NULL,
    computed_at   TIMESTAMPTZ NOT NULL
);

CREATE INDEX vehicle_positions_geom_gist ON vehicle_positions USING GIST (geom);
-- Delta streaming: "rows newer than the client's cursor".
CREATE INDEX vehicle_positions_computed_at_idx ON vehicle_positions (computed_at);

-- Cache of ST_LineLocatePoint(shape, stop): where each stop sits as a
-- fraction along a shape. VBB's shape_dist_traveled is unreliable/absent, so
-- fractions are computed geometrically — but not per tick: locating all
-- ~254k distinct (shape, stop) pairs takes ~40s of PostGIS time, so the
-- engine fills this cache lazily for the shapes its active trips reference
-- (an anti-join makes warm ticks free). The GTFS loader truncates it on
-- reload: fractions are only valid for the geometry they were computed on.
CREATE TABLE shape_stop_fractions (
    shape_id TEXT NOT NULL,
    stop_id  TEXT NOT NULL,
    fraction DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (shape_id, stop_id)
);
