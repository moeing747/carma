-- 0001: static GTFS schema (snapshot model: the loader truncate-and-reloads).

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE agencies (
    agency_id       TEXT PRIMARY KEY,
    agency_name     TEXT NOT NULL,
    agency_url      TEXT NOT NULL DEFAULT '',
    agency_timezone TEXT NOT NULL
);

CREATE TABLE routes (
    route_id         TEXT PRIMARY KEY,
    agency_id        TEXT NOT NULL DEFAULT '',
    route_short_name TEXT NOT NULL DEFAULT '',
    route_long_name  TEXT NOT NULL DEFAULT '',
    route_type       INTEGER NOT NULL
);

CREATE TABLE trips (
    trip_id       TEXT PRIMARY KEY,
    route_id      TEXT NOT NULL,
    service_id    TEXT NOT NULL,
    trip_headsign TEXT NOT NULL DEFAULT '',
    direction_id  SMALLINT,
    shape_id      TEXT,
    -- Denormalized stop_times span, populated by the loader. Makes
    -- "which trips are active at instant t" one indexed scan instead of
    -- an aggregate over millions of stop_times rows on every call.
    first_departure_seconds INTEGER,
    last_arrival_seconds    INTEGER
);

CREATE INDEX trips_route_id_idx ON trips (route_id);
CREATE INDEX trips_service_id_idx ON trips (service_id);

CREATE TABLE stops (
    stop_id        TEXT PRIMARY KEY,
    stop_name      TEXT NOT NULL DEFAULT '',
    parent_station TEXT,
    location_type  SMALLINT,
    geom           geometry(Point, 4326)
);

CREATE INDEX stops_geom_gist ON stops USING GIST (geom);

CREATE TABLE stop_times (
    trip_id             TEXT NOT NULL,
    stop_sequence       INTEGER NOT NULL,
    stop_id             TEXT NOT NULL,
    -- Seconds since service-day start, not TIME: GTFS clock times exceed
    -- 24:00:00 on trips running past midnight (e.g. 25:10:00).
    arrival_seconds     INTEGER,
    departure_seconds   INTEGER,
    shape_dist_traveled DOUBLE PRECISION,
    PRIMARY KEY (trip_id, stop_sequence)
);

CREATE TABLE shapes (
    shape_id TEXT PRIMARY KEY,
    geom     geometry(LineString, 4326) NOT NULL
);

CREATE INDEX shapes_geom_gist ON shapes USING GIST (geom);

-- COPY target for raw shapes.txt points; the loader assembles them into one
-- LineString per shape_id (ST_MakeLine) and truncates this table afterwards.
CREATE TABLE shape_points (
    shape_id          TEXT NOT NULL,
    shape_pt_sequence INTEGER NOT NULL,
    lat               DOUBLE PRECISION NOT NULL,
    lon               DOUBLE PRECISION NOT NULL
);

CREATE TABLE calendar (
    service_id TEXT PRIMARY KEY,
    monday     BOOLEAN NOT NULL,
    tuesday    BOOLEAN NOT NULL,
    wednesday  BOOLEAN NOT NULL,
    thursday   BOOLEAN NOT NULL,
    friday     BOOLEAN NOT NULL,
    saturday   BOOLEAN NOT NULL,
    sunday     BOOLEAN NOT NULL,
    start_date DATE NOT NULL,
    end_date   DATE NOT NULL
);

CREATE TABLE calendar_dates (
    service_id     TEXT NOT NULL,
    service_date   DATE NOT NULL,
    exception_type SMALLINT NOT NULL, -- 1 = service added, 2 = service removed
    PRIMARY KEY (service_id, service_date)
);

CREATE INDEX calendar_dates_service_date_idx ON calendar_dates (service_date);
