"""Console scripts: carma-migrate, carma-load-gtfs, carma-poll-feed,
and carma-consume-trip-updates."""

import argparse
import logging
import os
import signal
import threading
import time
from pathlib import Path
from types import FrameType

import psycopg

from carma.adapters.gtfs_rt import decode_trip_updates
from carma.adapters.gtfs_static import load_gtfs_zip
from carma.adapters.http_feed import HttpFeedSource
from carma.adapters.kafka import (
    KafkaTripUpdateConsumer,
    KafkaTripUpdatePublisher,
    ensure_topic,
)
from carma.adapters.migrations import apply_migrations
from carma.adapters.postgis_delays import PostgisFeedStatusRepository, PostgisTripDelayRepository
from carma.application.polling import PollSchedule
from carma.application.use_cases import ApplyTripDelays, IngestFeedSnapshot

_DEFAULT_FEED_URL = "https://production.gtfsrt.vbb.de/data"

_log = logging.getLogger("carma")


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set")
    return url


def _kafka_brokers() -> str:
    return os.environ.get("KAFKA_BROKERS", "localhost:9092")


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _stop_event_on_signals() -> threading.Event:
    """SIGTERM/SIGINT set the event; loops finish their cycle, then exit."""
    stop = threading.Event()

    def _handle(signum: int, _frame: FrameType | None) -> None:
        _log.info("event=shutdown_requested signal=%d", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    return stop


def migrate() -> None:
    parser = argparse.ArgumentParser(
        prog="carma-migrate",
        description="Apply pending SQL migrations to the database in DATABASE_URL.",
    )
    parser.add_argument(
        "--migrations-dir",
        type=Path,
        default=Path(os.environ.get("CARMA_MIGRATIONS_DIR", "migrations")),
        help="directory with numbered *.sql files (default: $CARMA_MIGRATIONS_DIR or ./migrations)",
    )
    args = parser.parse_args()
    with psycopg.connect(_database_url()) as conn:
        applied = apply_migrations(conn, args.migrations_dir)
    if applied:
        print(f"applied {len(applied)} migration(s): {', '.join(applied)}")
    else:
        print("schema up to date, nothing to apply")


def load_gtfs() -> None:
    parser = argparse.ArgumentParser(
        prog="carma-load-gtfs",
        description="Full reload of the static GTFS tables from a feed zip.",
    )
    parser.add_argument("zip_path", type=Path, help="path to a static GTFS zip")
    args = parser.parse_args()
    if not args.zip_path.is_file():
        raise SystemExit(f"no such file: {args.zip_path}")
    with psycopg.connect(_database_url()) as conn:
        report = load_gtfs_zip(conn, args.zip_path)
    for table, count in report.rows_loaded.items():
        print(f"{table}: {count} rows")


def poll_feed() -> None:
    parser = argparse.ArgumentParser(
        prog="carma-poll-feed",
        description="Poll the GTFS-RT feed and publish TripDelays to Kafka.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="poll a single time and exit (non-zero on failure)",
    )
    args = parser.parse_args()
    _configure_logging()

    feed_url = os.environ.get("CARMA_FEED_URL", _DEFAULT_FEED_URL)
    interval = float(os.environ.get("CARMA_POLL_INTERVAL_SECONDS", "30"))
    brokers = _kafka_brokers()

    ensure_topic(brokers)
    publisher = KafkaTripUpdatePublisher(brokers)
    ingest = IngestFeedSnapshot(
        source=HttpFeedSource(feed_url),
        decode=decode_trip_updates,
        publisher=publisher,
    )
    schedule = PollSchedule(interval_seconds=interval)
    stop = _stop_event_on_signals()
    _log.info("event=poller_started url=%s interval_seconds=%s", feed_url, interval)

    failures = 0
    try:
        while not stop.is_set():
            started = time.monotonic()
            try:
                published = ingest.execute()
                # flush inside the cycle so delivery failures surface now,
                # not after another 30s of silently queued messages.
                publisher.flush()
                failures = 0
                elapsed_ms = int((time.monotonic() - started) * 1000)
                if published is None:
                    _log.info("event=feed_polled result=not_modified elapsed_ms=%d", elapsed_ms)
                else:
                    _log.info(
                        "event=feed_polled result=published entities=%d elapsed_ms=%d",
                        published,
                        elapsed_ms,
                    )
            except Exception:
                failures += 1
                _log.exception("event=feed_poll_failed consecutive_failures=%d", failures)
                if args.once:
                    raise SystemExit(1) from None
            if args.once:
                break
            stop.wait(schedule.next_delay(time.monotonic() - started, failures))
    finally:
        publisher.close()
    _log.info("event=poller_stopped")


def consume_trip_updates() -> None:
    argparse.ArgumentParser(
        prog="carma-consume-trip-updates",
        description="Consume TripDelays from Kafka into PostGIS (latest per trip).",
    ).parse_args()
    _configure_logging()

    brokers = _kafka_brokers()
    ensure_topic(brokers)
    stop = _stop_event_on_signals()
    # autocommit=True: the repositories open explicit per-write transactions.
    with psycopg.connect(_database_url(), autocommit=True) as conn:
        apply = ApplyTripDelays(
            repository=PostgisTripDelayRepository(conn=conn),
            feed_status=PostgisFeedStatusRepository(conn=conn),
        )
        consumer = KafkaTripUpdateConsumer(brokers)
        _log.info("event=consumer_started brokers=%s", brokers)
        try:
            consumer.run(apply.execute, stop)
        except Exception:
            # Offsets for the failed batch were not committed; exiting non-zero
            # lets the supervisor restart us from the last committed offset.
            _log.exception("event=consumer_failed")
            raise SystemExit(1) from None
        finally:
            consumer.close()
    _log.info("event=consumer_stopped")
