from collections.abc import Collection
from datetime import UTC, datetime, timedelta

from carma.application.use_cases import (
    ApplyTripDelays,
    IngestFeedSnapshot,
    RecomputePositions,
    RecomputeReport,
)
from carma.domain.models import Coordinate, ScheduledStop, TripDelay, TripId

BASE_TIME = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def _delay(trip_id: str, at: datetime = BASE_TIME) -> TripDelay:
    return TripDelay(
        trip_id=TripId(trip_id),
        route_id="27288_700",
        timestamp=at,
        stop_time_events=(),
    )


class StaticFeedSource:
    def __init__(self, payload: bytes | None = b"snapshot") -> None:
        self.payload = payload

    def fetch(self) -> bytes | None:
        return self.payload


class RecordingPublisher:
    def __init__(self) -> None:
        self.published: list[TripDelay] = []

    def publish(self, delay: TripDelay) -> None:
        self.published.append(delay)


class RecordingDelayRepository:
    def __init__(self) -> None:
        self.saved: list[TripDelay] = []

    def save(self, delay: TripDelay) -> None:
        self.saved.append(delay)

    def latest_for_trip(self, trip_id: TripId) -> TripDelay | None:
        matches = [delay for delay in self.saved if delay.trip_id == trip_id]
        return matches[-1] if matches else None


class RecordingFeedStatusRepository:
    def __init__(self) -> None:
        self.snapshots: list[tuple[datetime, int]] = []

    def record_snapshot(self, snapshot_at: datetime, entity_count: int) -> None:
        self.snapshots.append((snapshot_at, entity_count))

    def latest(self) -> None:
        return None


def test_ingest_publishes_every_decoded_delay_and_returns_count() -> None:
    decoded = [_delay("trip-1"), _delay("trip-2"), _delay("trip-3")]
    publisher = RecordingPublisher()
    use_case = IngestFeedSnapshot(
        source=StaticFeedSource(),
        decode=lambda payload: decoded if payload == b"snapshot" else [],
        publisher=publisher,
    )

    assert use_case.execute() == 3
    assert publisher.published == decoded


def test_ingest_of_empty_snapshot_publishes_nothing() -> None:
    publisher = RecordingPublisher()
    use_case = IngestFeedSnapshot(
        source=StaticFeedSource(),
        decode=lambda _: [],
        publisher=publisher,
    )

    assert use_case.execute() == 0
    assert publisher.published == []


def test_ingest_of_unchanged_feed_skips_decode_and_publish() -> None:
    publisher = RecordingPublisher()

    def explode(_: bytes) -> list[TripDelay]:
        raise AssertionError("decode must not run for an unchanged feed")

    use_case = IngestFeedSnapshot(
        source=StaticFeedSource(payload=None),
        decode=explode,
        publisher=publisher,
    )

    assert use_case.execute() is None
    assert publisher.published == []


def test_apply_saves_each_delay_and_advances_feed_status() -> None:
    repository = RecordingDelayRepository()
    feed_status = RecordingFeedStatusRepository()
    newest = BASE_TIME + timedelta(seconds=30)
    batch = [_delay("trip-1"), _delay("trip-2", at=newest), _delay("trip-3")]

    ApplyTripDelays(repository=repository, feed_status=feed_status).execute(batch)

    assert repository.saved == batch
    # status carries the newest feed timestamp in the batch and its size
    assert feed_status.snapshots == [(newest, 3)]


def test_apply_of_empty_batch_touches_nothing() -> None:
    repository = RecordingDelayRepository()
    feed_status = RecordingFeedStatusRepository()

    ApplyTripDelays(repository=repository, feed_status=feed_status).execute([])

    assert repository.saved == []
    assert feed_status.snapshots == []


class StaticScheduleRepository:
    def __init__(self, active: frozenset[TripId]) -> None:
        self.active = active
        self.asked_at: list[datetime] = []

    def active_trip_ids(self, at: datetime) -> frozenset[TripId]:
        self.asked_at.append(at)
        return self.active

    def schedule_for_trip(self, trip_id: TripId) -> tuple[ScheduledStop, ...]:
        return ()

    def shape_for_trip(self, trip_id: TripId) -> tuple[Coordinate, ...] | None:
        return None


class RecordingEngine:
    def __init__(self, written: int) -> None:
        self.written = written
        self.calls: list[tuple[frozenset[TripId], datetime]] = []

    def recompute(self, trip_ids: Collection[TripId], at: datetime) -> int:
        self.calls.append((frozenset(trip_ids), at))
        return self.written


def test_recompute_positions_hands_active_trips_to_the_engine() -> None:
    active = frozenset({TripId("trip-1"), TripId("trip-2")})
    schedule = StaticScheduleRepository(active)
    engine = RecordingEngine(written=2)

    report = RecomputePositions(schedule=schedule, engine=engine).execute(BASE_TIME)

    assert schedule.asked_at == [BASE_TIME]
    assert engine.calls == [(active, BASE_TIME)]
    assert report == RecomputeReport(active_trips=2, positions_written=2)
