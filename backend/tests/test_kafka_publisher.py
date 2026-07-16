"""Unit tests for delivery-report bookkeeping; no broker involved."""

from datetime import UTC, datetime

import pytest
from confluent_kafka import KafkaError

from carma.adapters.kafka import DeliveryFailedError, KafkaTripUpdatePublisher
from carma.domain.models import TripDelay, TripId

DELAY = TripDelay(
    trip_id=TripId("trip-1"),
    route_id="r1",
    timestamp=datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
    stop_time_events=(),
)


class FakeMessage:
    def topic(self) -> str:
        return "trip-updates"

    def key(self) -> bytes:
        return b"r1:trip-1"


def test_flush_raises_when_messages_remain_undelivered() -> None:
    # No broker behind this address: the message stays queued past the
    # flush timeout and must be reported, not silently dropped.
    publisher = KafkaTripUpdatePublisher("localhost:1")
    publisher.publish(DELAY)

    with pytest.raises(DeliveryFailedError, match="undelivered"):
        publisher.flush(timeout_seconds=0.3)


def test_failed_delivery_report_surfaces_on_next_publish() -> None:
    publisher = KafkaTripUpdatePublisher("localhost:1")
    error = KafkaError(KafkaError._MSG_TIMED_OUT)

    # Simulate librdkafka invoking the delivery callback with a failure.
    publisher._on_delivery(error, FakeMessage())  # type: ignore[arg-type]

    with pytest.raises(DeliveryFailedError):
        publisher.publish(DELAY)
    # The error is consumed once surfaced; the publisher is usable again.
    publisher.publish(DELAY)
