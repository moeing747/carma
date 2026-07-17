"""Full pipeline lifecycle: one delay snapshot travels the whole system.

Testcontainers Kafka + PostGIS, real migrations, the mini GTFS fixture loaded
by the real loader — then the same orchestrating code the console scripts
drive, one shot each: IngestFeedSnapshot (the carma-poll-feed cycle) over an
in-memory GTFS-RT protobuf, KafkaTripUpdateConsumer.process_batch +
ApplyTripDelays (the carma-consume-trip-updates iteration), and
RecomputePositions (the carma-project-positions tick). Every end-state
assertion goes through the Flask API wired to the same database.

Fixture timetable (Europe/Berlin, service day Tue 2026-07-14): T1 on route R1
"M1" with shape SH1, S1 08:00 -> S2 08:10/08:11 -> S3 08:20/08:21 -> S4 08:30.
The probe instant is 08:12, mid-trip; snapshots delay S2 so the vehicle is
travelling toward a pushed-back arrival.
"""

import time
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from google.transit import gtfs_realtime_pb2
from testcontainers.kafka import KafkaContainer
from testcontainers.postgres import PostgresContainer

from carma.adapters.gtfs_rt import decode_trip_updates
from carma.adapters.gtfs_static import load_gtfs_zip
from carma.adapters.kafka import (
    KafkaTripUpdateConsumer,
    KafkaTripUpdatePublisher,
    ensure_topic,
)
from carma.adapters.migrations import apply_migrations
from carma.adapters.postgis_delays import PostgisFeedStatusRepository, PostgisTripDelayRepository
from carma.adapters.postgis_positions import PostgisPositionEngine, PostgisVehiclePositionReader
from carma.adapters.postgis_schedule import PostgisTripScheduleRepository
from carma.application.use_cases import (
    ApplyTripDelays,
    IngestFeedSnapshot,
    RecomputePositions,
    RecomputeReport,
)
from carma.domain.models import StopTimeEvent, TripDelay, TripId
from carma.domain.positioning import derive_position
from carma.entrypoints.api import create_app
from tests.gtfs_fixture import write_fixture_zip

pytestmark = pytest.mark.integration

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"
DRAIN_DEADLINE_SECONDS = 90.0
BERLIN_BBOX = "13.3,52.4,13.5,52.6"

T1 = TripId("T1")
AT = datetime(2026, 7, 14, 8, 12)  # naive feed-local probe instant, mid-T1
AT_SECONDS = 8 * 3600 + 12 * 60  # seconds into T1's service day at AT

# Feed-side wall clock: 08:11 Berlin summer time is 06:11 UTC, so the first
# snapshot is published one minute before the probe instant.
SNAPSHOT_1_AT = datetime(2026, 7, 14, 6, 11, 0, tzinfo=UTC)
SNAPSHOT_2_AT = SNAPSHOT_1_AT + timedelta(seconds=150)

T1_EVENTS_1 = (
    StopTimeEvent(
        stop_id="S2", stop_sequence=2, arrival_delay_seconds=240, departure_delay_seconds=300
    ),
)
T1_EVENTS_2 = (
    StopTimeEvent(
        stop_id="S2", stop_sequence=2, arrival_delay_seconds=480, departure_delay_seconds=540
    ),
)

SNAPSHOT_1 = (
    TripDelay(trip_id=T1, route_id="R1", timestamp=SNAPSHOT_1_AT, stop_time_events=T1_EVENTS_1),
    # T2 rides along to prove multi-entity snapshots flow; it is not active
    # at 08:12 (it runs 23:50 -> 25:10), so it never gets a position.
    TripDelay(
        trip_id=TripId("T2"),
        route_id="R1",
        timestamp=SNAPSHOT_1_AT,
        stop_time_events=(
            StopTimeEvent(
                stop_id="S5",
                stop_sequence=2,
                arrival_delay_seconds=120,
                departure_delay_seconds=120,
            ),
        ),
    ),
)
SNAPSHOT_2 = (
    TripDelay(trip_id=T1, route_id="R1", timestamp=SNAPSHOT_2_AT, stop_time_events=T1_EVENTS_2),
)


def _encode_feed(delays: Sequence[TripDelay]) -> bytes:
    """A serialized GTFS-RT FeedMessage — the inverse of decode_trip_updates."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = max(int(delay.timestamp.timestamp()) for delay in delays)
    for delay in delays:
        entity = feed.entity.add()
        entity.id = delay.trip_id.value
        trip_update = entity.trip_update
        trip_update.trip.trip_id = delay.trip_id.value
        trip_update.trip.route_id = delay.route_id
        trip_update.timestamp = int(delay.timestamp.timestamp())
        for event in delay.stop_time_events:
            update = trip_update.stop_time_update.add()
            update.stop_id = event.stop_id
            update.stop_sequence = event.stop_sequence
            if event.arrival_delay_seconds is not None:
                update.arrival.delay = event.arrival_delay_seconds
            if event.departure_delay_seconds is not None:
                update.departure.delay = event.departure_delay_seconds
    payload: bytes = feed.SerializeToString()
    return payload


class InMemoryFeedSource:
    """FeedSource port over bytes in memory: fetch() pops the next pushed
    snapshot and otherwise reports the feed unchanged (None), exactly like
    HttpFeedSource answering a 304."""

    def __init__(self) -> None:
        self._pending: deque[bytes] = deque()

    def push(self, payload: bytes) -> None:
        self._pending.append(payload)

    def fetch(self) -> bytes | None:
        return self._pending.popleft() if self._pending else None


@dataclass
class ManualClock:
    """The API's injectable UTC wall clock, advanced by hand."""

    now: datetime

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture(scope="module")
def bootstrap() -> Iterator[str]:
    with KafkaContainer() as kafka:
        server = kafka.get_bootstrap_server()
        ensure_topic(server)
        yield server


@pytest.fixture(scope="module")
def conn(tmp_path_factory: pytest.TempPathFactory) -> Iterator[psycopg.Connection[Any]]:
    fixture_zip = write_fixture_zip(tmp_path_factory.mktemp("gtfs") / "gtfs-mini.zip")
    with PostgresContainer("postgis/postgis:16-3.4") as container:
        url = (
            f"postgresql://{container.username}:{container.password}"
            f"@{container.get_container_host_ip()}:{container.get_exposed_port(5432)}"
            f"/{container.dbname}"
        )
        # autocommit: the repositories and engine run their own transactions.
        with psycopg.connect(url, autocommit=True) as connection:
            apply_migrations(connection, MIGRATIONS_DIR)
            load_gtfs_zip(connection, fixture_zip)
            yield connection


@pytest.fixture()
def publisher(bootstrap: str) -> Iterator[KafkaTripUpdatePublisher]:
    kafka_publisher = KafkaTripUpdatePublisher(bootstrap)
    yield kafka_publisher
    kafka_publisher.close()


@pytest.fixture()
def consumer(bootstrap: str) -> Iterator[KafkaTripUpdateConsumer]:
    kafka_consumer = KafkaTripUpdateConsumer(
        bootstrap, group_id="carma-pipeline-lifecycle", poll_timeout_seconds=2.0
    )
    yield kafka_consumer
    kafka_consumer.close()


def _drain_until(
    consumer: KafkaTripUpdateConsumer,
    apply: ApplyTripDelays,
    done: Callable[[], bool],
) -> None:
    deadline = time.monotonic() + DRAIN_DEADLINE_SECONDS
    while time.monotonic() < deadline:
        consumer.process_batch(apply.execute)
        if done():
            return
    raise AssertionError("condition not reached before drain deadline")


def _meters_between(
    conn: psycopg.Connection[Any], lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    row = conn.execute(
        "SELECT ST_Distance(ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,"
        " ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography)",
        (lon1, lat1, lon2, lat2),
    ).fetchone()
    assert row is not None
    return float(row[0])


def _meters_off_shape(conn: psycopg.Connection[Any], lat: float, lon: float) -> float:
    row = conn.execute(
        "SELECT ST_Distance(geom::geography,"
        " ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography)"
        " FROM shapes WHERE shape_id = 'SH1'",
        (lon, lat),
    ).fetchone()
    assert row is not None
    return float(row[0])


def _shape_fraction(conn: psycopg.Connection[Any], lat: float, lon: float) -> float:
    row = conn.execute(
        "SELECT ST_LineLocatePoint(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))"
        " FROM shapes WHERE shape_id = 'SH1'",
        (lon, lat),
    ).fetchone()
    assert row is not None
    return float(row[0])


def test_one_snapshot_travels_the_whole_pipeline(
    conn: psycopg.Connection[Any],
    publisher: KafkaTripUpdatePublisher,
    consumer: KafkaTripUpdateConsumer,
) -> None:
    delay_repo = PostgisTripDelayRepository(conn=conn)
    status_repo = PostgisFeedStatusRepository(conn=conn)
    schedule = PostgisTripScheduleRepository(conn=conn)
    clock = ManualClock(now=SNAPSHOT_1_AT + timedelta(seconds=40))
    app = create_app(
        feed_status_source=lambda: status_repo.latest(),
        position_reader_factory=lambda: nullcontext(PostgisVehiclePositionReader(conn=conn)),
        schedule_repository_factory=lambda: nullcontext(schedule),
        utc_now_source=lambda: clock.now,
    )
    client = app.test_client()

    # Step 1: poll — the carma-poll-feed cycle over an in-memory GTFS-RT feed.
    source = InMemoryFeedSource()
    ingest = IngestFeedSnapshot(source=source, decode=decode_trip_updates, publisher=publisher)
    source.push(_encode_feed(SNAPSHOT_1))
    assert ingest.execute() == len(SNAPSHOT_1)
    publisher.flush()
    assert ingest.execute() is None  # unchanged feed: nothing republished

    # Step 2: consume — the carma-consume-trip-updates iteration, into PostGIS.
    apply = ApplyTripDelays(repository=delay_repo, feed_status=status_repo)
    _drain_until(
        consumer,
        apply,
        lambda: all(delay_repo.latest_for_trip(d.trip_id) is not None for d in SNAPSHOT_1),
    )
    # Full round trip: protobuf -> real decoder -> Kafka -> JSONB, bit for bit.
    assert delay_repo.latest_for_trip(T1) == SNAPSHOT_1[0]
    status = status_repo.latest()
    assert status is not None and status.last_snapshot_at == SNAPSHOT_1_AT

    # Step 3: project — one carma-project-positions tick at the probe instant.
    recompute = RecomputePositions(schedule=schedule, engine=PostgisPositionEngine(conn=conn))
    assert recompute.execute(AT) == RecomputeReport(active_trips=1, positions_written=1)

    # Step 4: read through the API — the vehicle is on its shape, delayed.
    body = client.get(f"/api/v1/positions?bbox={BERLIN_BBOX}").get_json()
    assert body["count"] == 1
    first = body["positions"][0]
    assert first["trip_id"] == "T1"
    assert first["route_short_name"] == "M1"
    assert first["headsign"] == "Hauptbahnhof"
    assert first["delay_seconds"] == 240  # travelling toward S2's delayed arrival
    assert _meters_off_shape(conn, first["lat"], first["lon"]) < 5.0
    # ... and exactly where schedule + the ingested delay say it should be.
    reference = derive_position(
        schedule.schedule_for_trip(T1), T1_EVENTS_1, schedule.shape_for_trip(T1), AT_SECONDS
    )
    assert reference is not None and reference.delay_seconds == 240
    assert (
        _meters_between(
            conn, first["lat"], first["lon"], reference.coordinate.lat, reference.coordinate.lon
        )
        < 5.0
    )

    feed = client.get("/api/v1/feed").get_json()
    assert feed["state"] == "fresh" and feed["fresh"] is True
    assert feed["age_seconds"] == 40.0
    assert datetime.fromisoformat(feed["last_snapshot_at"]) == SNAPSHOT_1_AT

    trip_schedule = client.get("/api/v1/trips/T1/schedule")
    assert trip_schedule.status_code == 200
    stops_body = trip_schedule.get_json()
    assert stops_body["delay_seconds"] == 240  # the live delay joined in
    assert [stop["name"] for stop in stops_body["stops"]] == [
        "Alexanderplatz",
        "Hackescher Markt",
        "Friedrichstr.",
        "Hauptbahnhof",
    ]
    assert stops_body["stops"][0]["departure"] == "08:00"

    # Step 5: staleness — 180s of silence pushes past the 120s window.
    clock.advance(140)
    feed = client.get("/api/v1/feed").get_json()
    assert feed["state"] == "stale" and feed["fresh"] is False
    assert feed["age_seconds"] == 180.0
    health = client.get("/health")  # liveness stays 200 on a stale feed
    assert health.status_code == 200 and health.get_json()["feed"]["state"] == "stale"

    # Step 6: a newer snapshot — the delay grows, the vehicle falls back,
    # and freshness recovers. Same poll/consume/project code, second pass.
    source.push(_encode_feed(SNAPSHOT_2))
    assert ingest.execute() == 1
    publisher.flush()
    _drain_until(consumer, apply, lambda: delay_repo.latest_for_trip(T1) == SNAPSHOT_2[0])
    assert recompute.execute(AT) == RecomputeReport(active_trips=1, positions_written=1)

    body = client.get(f"/api/v1/positions?bbox={BERLIN_BBOX}").get_json()
    assert body["count"] == 1
    second = body["positions"][0]
    assert second["delay_seconds"] == 480
    assert _meters_off_shape(conn, second["lat"], second["lon"]) < 5.0
    # Same instant, doubled delay: the vehicle sits further back on the shape.
    assert _shape_fraction(conn, second["lat"], second["lon"]) < _shape_fraction(
        conn, first["lat"], first["lon"]
    )

    feed = client.get("/api/v1/feed").get_json()
    assert feed["state"] == "fresh" and feed["fresh"] is True
    assert feed["age_seconds"] == 30.0  # snapshot 2 landed 150s in, clock is at 180s
