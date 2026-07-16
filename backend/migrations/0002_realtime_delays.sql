-- 0002: realtime delay storage (latest-state model, not an event log).
--
-- trip_delays holds only the LATEST TripDelay per trip. Feed snapshots
-- overlap by design, so writes are latest-wins upserts: a snapshot with an
-- older-or-equal feed_timestamp must never regress a row (replay-safe).
--
-- stop_time_events is a JSONB array ordered by stop_sequence:
--   [{"stop_id": text, "stop_sequence": int,
--     "arrival_delay_seconds": int|null, "departure_delay_seconds": int|null}, ...]
-- A document column instead of a child table: Phase 3 (position derivation)
-- always reads a trip's events as one unit, never queries individual events.

CREATE TABLE trip_delays (
    trip_id          TEXT PRIMARY KEY,
    route_id         TEXT NOT NULL,
    feed_timestamp   TIMESTAMPTZ NOT NULL,
    received_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    stop_time_events JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX trip_delays_route_id_idx ON trip_delays (route_id);

-- Single-row pipeline status; the consumer refreshes it with every applied
-- batch. /health derives feed freshness from last_snapshot_at (fresh when
-- within 120 seconds). last_snapshot_at only ever advances (GREATEST on
-- update), so replays of old data cannot fake freshness.
CREATE TABLE feed_status (
    id                BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    last_snapshot_at  TIMESTAMPTZ NOT NULL,
    last_entity_count INTEGER NOT NULL,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
