from datetime import UTC, datetime

from carma.application.use_cases import IngestFeedSnapshot
from carma.domain.models import TripDelay, TripId


def _delay(trip_id: str) -> TripDelay:
    return TripDelay(
        trip_id=TripId(trip_id),
        route_id="27288_700",
        timestamp=datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
        stop_time_events=(),
    )


class StaticFeedSource:
    def fetch(self) -> bytes:
        return b"snapshot"


class RecordingPublisher:
    def __init__(self) -> None:
        self.published: list[TripDelay] = []

    def publish(self, delay: TripDelay) -> None:
        self.published.append(delay)


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
