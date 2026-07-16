"""Testcontainers Kafka + PostGIS: the realtime pipeline end to end.

Publishes TripDelays decoded from the live-feed fixture, consumes them into
PostGIS, and verifies the two pipeline invariants: overlapping-snapshot
replays are idempotent (latest-wins), and poison messages are skipped
without wedging the consumer.

Tests in this module run in order and share the containers (same pattern as
test_postgis_integration.py).
"""

import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from confluent_kafka import Producer
from testcontainers.kafka import KafkaContainer
from testcontainers.postgres import PostgresContainer

from carma.adapters.gtfs_rt import decode_trip_updates
from carma.adapters.kafka import (
    TRIP_UPDATES_TOPIC,
    KafkaTripUpdateConsumer,
    KafkaTripUpdatePublisher,
    ensure_topic,
)
from carma.adapters.migrations import apply_migrations
from carma.adapters.postgis_delays import PostgisFeedStatusRepository, PostgisTripDelayRepository
from carma.application.use_cases import ApplyTripDelays
from carma.domain.models import StopTimeEvent, TripDelay, TripId

pytestmark = pytest.mark.integration

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"
FIXTURE = Path(__file__).parent / "fixtures" / "vbb-tripupdates-sample.pb"
SNAPSHOT_SIZE = 20
DRAIN_DEADLINE_SECONDS = 90.0


def _unique_by_trip(delays: list[TripDelay]) -> list[TripDelay]:
    seen: set[TripId] = set()
    unique = []
    for delay in delays:
        if delay.trip_id not in seen:
            seen.add(delay.trip_id)
            unique.append(delay)
    return unique


SNAPSHOT_A = _unique_by_trip(decode_trip_updates(FIXTURE.read_bytes()))[:SNAPSHOT_SIZE]


def _bump_event(event: StopTimeEvent) -> StopTimeEvent:
    if event.arrival_delay_seconds is None:
        return event
    return replace(event, arrival_delay_seconds=event.arrival_delay_seconds + 60)


# The overlapping follow-up snapshot: same trips, republished 60s later with
# changed delays -- exactly how consecutive VBB snapshots overlap.
SNAPSHOT_B = [
    replace(
        delay,
        timestamp=delay.timestamp + timedelta(seconds=60),
        stop_time_events=tuple(_bump_event(event) for event in delay.stop_time_events),
    )
    for delay in SNAPSHOT_A
]


@pytest.fixture(scope="module")
def bootstrap() -> Iterator[str]:
    with KafkaContainer() as kafka:
        server = kafka.get_bootstrap_server()
        ensure_topic(server)
        yield server


@pytest.fixture(scope="module")
def conn() -> Iterator[psycopg.Connection[Any]]:
    with PostgresContainer("postgis/postgis:16-3.4") as container:
        url = (
            f"postgresql://{container.username}:{container.password}"
            f"@{container.get_container_host_ip()}:{container.get_exposed_port(5432)}"
            f"/{container.dbname}"
        )
        # autocommit: the delay repositories run their own transactions.
        with psycopg.connect(url, autocommit=True) as connection:
            apply_migrations(connection, MIGRATIONS_DIR)
            yield connection


@pytest.fixture(scope="module")
def publisher(bootstrap: str) -> Iterator[KafkaTripUpdatePublisher]:
    kafka_publisher = KafkaTripUpdatePublisher(bootstrap)
    yield kafka_publisher
    kafka_publisher.close()


@pytest.fixture(scope="module")
def consumer(bootstrap: str) -> Iterator[KafkaTripUpdateConsumer]:
    kafka_consumer = KafkaTripUpdateConsumer(
        bootstrap, group_id="carma-integration", poll_timeout_seconds=2.0
    )
    yield kafka_consumer
    kafka_consumer.close()


@pytest.fixture()
def apply(conn: psycopg.Connection[Any]) -> ApplyTripDelays:
    return ApplyTripDelays(
        repository=PostgisTripDelayRepository(conn=conn),
        feed_status=PostgisFeedStatusRepository(conn=conn),
    )


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


def _drain_messages(
    consumer: KafkaTripUpdateConsumer, apply: ApplyTripDelays, expected: int
) -> None:
    consumed = 0
    deadline = time.monotonic() + DRAIN_DEADLINE_SECONDS
    while consumed < expected and time.monotonic() < deadline:
        consumed += consumer.process_batch(apply.execute)
    assert consumed >= expected


def _row_count(conn: psycopg.Connection[Any]) -> int:
    row = conn.execute("SELECT count(*) FROM trip_delays").fetchone()
    assert row is not None
    return int(row[0])


def test_published_snapshot_lands_in_postgis(
    publisher: KafkaTripUpdatePublisher,
    consumer: KafkaTripUpdateConsumer,
    apply: ApplyTripDelays,
    conn: psycopg.Connection[Any],
) -> None:
    assert SNAPSHOT_A, "fixture must decode to at least one TripDelay"
    for delay in SNAPSHOT_A:
        publisher.publish(delay)
    publisher.flush()

    _drain_until(consumer, apply, lambda: _row_count(conn) == len(SNAPSHOT_A))

    sample = SNAPSHOT_A[0]
    stored = PostgisTripDelayRepository(conn=conn).latest_for_trip(sample.trip_id)
    assert stored == sample  # full round trip incl. JSONB stop_time_events

    status = PostgisFeedStatusRepository(conn=conn).latest()
    assert status is not None
    assert status.last_snapshot_at == max(delay.timestamp for delay in SNAPSHOT_A)


def test_overlapping_snapshot_replay_is_idempotent(
    publisher: KafkaTripUpdatePublisher,
    consumer: KafkaTripUpdateConsumer,
    apply: ApplyTripDelays,
    conn: psycopg.Connection[Any],
) -> None:
    repo = PostgisTripDelayRepository(conn=conn)
    sample = SNAPSHOT_B[0]

    # Newer overlapping snapshot: every row advances, no new rows appear.
    for delay in SNAPSHOT_B:
        publisher.publish(delay)
    publisher.flush()
    _drain_until(consumer, apply, lambda: repo.latest_for_trip(sample.trip_id) == sample)
    assert _row_count(conn) == len(SNAPSHOT_B)

    # Full replay of the OLDER snapshot: consumed, but no row regresses.
    for delay in SNAPSHOT_A:
        publisher.publish(delay)
    publisher.flush()
    _drain_messages(consumer, apply, expected=len(SNAPSHOT_A))

    for delay in SNAPSHOT_B:
        assert repo.latest_for_trip(delay.trip_id) == delay

    status = PostgisFeedStatusRepository(conn=conn).latest()
    assert status is not None
    # GREATEST guard: the replay did not move freshness backwards.
    assert status.last_snapshot_at == max(delay.timestamp for delay in SNAPSHOT_B)


def test_poison_message_is_skipped_and_consumer_survives(
    bootstrap: str,
    publisher: KafkaTripUpdatePublisher,
    consumer: KafkaTripUpdateConsumer,
    apply: ApplyTripDelays,
    conn: psycopg.Connection[Any],
) -> None:
    raw = Producer({"bootstrap.servers": bootstrap})
    raw.produce(TRIP_UPDATES_TOPIC, value=b"\xff\x00 not a trip delay")
    raw.produce(TRIP_UPDATES_TOPIC, value=b'{"trip_id": "half", "route_id": "baked"}')
    raw.flush(10)

    survivor = TripDelay(
        trip_id=TripId("IT-POISON-SURVIVOR"),
        route_id="r-test",
        timestamp=datetime.now(tz=UTC),
        stop_time_events=(),
    )
    publisher.publish(survivor)
    publisher.flush()

    repo = PostgisTripDelayRepository(conn=conn)
    _drain_until(consumer, apply, lambda: repo.latest_for_trip(survivor.trip_id) is not None)

    # The consumer is still functional after the poison batch.
    assert consumer.process_batch(apply.execute) >= 0
    assert _row_count(conn) == len(SNAPSHOT_A) + 1
