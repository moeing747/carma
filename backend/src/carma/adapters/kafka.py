"""Kafka edge: topic bootstrap, TripUpdate producer, and consumer.

Delivery semantics are at-least-once end to end; the PostGIS upsert being
latest-wins (idempotent) makes redelivery harmless.
"""

import logging
import threading
import time
from collections.abc import Callable, Sequence

from confluent_kafka import Consumer, KafkaError, KafkaException, Message, Producer
from confluent_kafka.admin import AdminClient
from confluent_kafka.cimpl import NewTopic

from carma.adapters.trip_delay_json import (
    deserialize_trip_delay,
    message_key,
    serialize_trip_delay,
)
from carma.domain.errors import FeedDecodeError
from carma.domain.models import TripDelay

TRIP_UPDATES_TOPIC = "trip-updates"
_TOPIC_PARTITIONS = 3

_log = logging.getLogger(__name__)


class DeliveryFailedError(Exception):
    """A produced message was not acknowledged by the broker."""


def ensure_topic(
    bootstrap_servers: str,
    topic: str = TRIP_UPDATES_TOPIC,
    partitions: int = _TOPIC_PARTITIONS,
    attempts: int = 5,
    retry_delay_seconds: float = 2.0,
) -> None:
    """Idempotently create the topic and wait until its metadata is served.

    Bounded retry loop: on first boot, producer/consumer services race the
    broker's metadata propagation and can see "does not host this
    topic-partition" for a freshly created topic. Retrying the idempotent
    ensure beats crash-looping the whole service.
    """
    admin = AdminClient({"bootstrap.servers": bootstrap_servers})
    for attempt in range(1, attempts + 1):
        try:
            _create_topic_if_missing(admin, topic, partitions)
            _assert_topic_served(admin, topic)
            return
        except KafkaException as exc:
            if attempt >= attempts:
                raise
            _log.warning(
                "event=ensure_topic_retry topic=%s attempt=%d error=%s",
                topic,
                attempt,
                exc,
            )
            time.sleep(retry_delay_seconds * attempt)


def _create_topic_if_missing(admin: AdminClient, topic: str, partitions: int) -> None:
    new_topic = NewTopic(topic, num_partitions=partitions, replication_factor=1)
    future = admin.create_topics([new_topic], operation_timeout=10.0)[topic]
    try:
        future.result(timeout=15.0)
    except KafkaException as exc:
        error: KafkaError = exc.args[0]
        if error.code() != KafkaError.TOPIC_ALREADY_EXISTS:
            raise


def _assert_topic_served(admin: AdminClient, topic: str) -> None:
    metadata = admin.list_topics(topic=topic, timeout=10.0)
    topic_metadata = metadata.topics.get(topic)
    if topic_metadata is None or topic_metadata.error is not None:
        raise KafkaException(
            topic_metadata.error
            if topic_metadata is not None
            else KafkaError(KafkaError.UNKNOWN_TOPIC_OR_PART)
        )
    unled = [p.id for p in topic_metadata.partitions.values() if p.leader < 0]
    if not topic_metadata.partitions or unled:
        raise KafkaException(KafkaError(KafkaError.LEADER_NOT_AVAILABLE))


class KafkaTripUpdatePublisher:
    """TripUpdatePublisher port over confluent-kafka.

    produce() is asynchronous; delivery reports arrive on later poll()/flush()
    calls. A failed report is remembered and surfaced as DeliveryFailedError
    on the next publish() or flush(), so the poll loop sees the failure within
    one cycle instead of silently dropping data.
    """

    def __init__(self, bootstrap_servers: str, topic: str = TRIP_UPDATES_TOPIC) -> None:
        self._producer = Producer(
            {
                "bootstrap.servers": bootstrap_servers,
                # Broker-side dedup on retries; free correctness for a demo.
                "enable.idempotence": True,
                # Route librdkafka's own logs through structured logging
                # instead of raw stderr.
                "logger": _log,
            }
        )
        self._topic = topic
        self._delivery_error: KafkaError | None = None

    def publish(self, delay: TripDelay) -> None:
        self._raise_if_delivery_failed()
        self._producer.produce(
            self._topic,
            key=message_key(delay),
            value=serialize_trip_delay(delay),
            on_delivery=self._on_delivery,
        )
        # Serve queued delivery reports without blocking the publish path.
        self._producer.poll(0)

    def flush(self, timeout_seconds: float = 30.0) -> None:
        remaining = self._producer.flush(timeout_seconds)
        if remaining:
            raise DeliveryFailedError(f"{remaining} message(s) still undelivered after flush")
        self._raise_if_delivery_failed()

    def close(self) -> None:
        self.flush()

    def _on_delivery(self, error: KafkaError | None, message: Message) -> None:
        if error is not None:
            self._delivery_error = error
            _log.error(
                "event=delivery_failed topic=%s key=%r error=%s",
                message.topic(),
                message.key(),
                error,
            )

    def _raise_if_delivery_failed(self) -> None:
        if self._delivery_error is not None:
            error, self._delivery_error = self._delivery_error, None
            raise DeliveryFailedError(str(error))


class KafkaTripUpdateConsumer:
    """Manual-commit consumer for the trip-updates topic.

    Offset handling (auto-commit off):
    - offsets are committed only AFTER the handler has processed the batch;
    - poison messages (undecodable values) are deterministic failures: logged,
      skipped, and included in the commit -- they must never crash or wedge
      the consumer;
    - a handler failure propagates WITHOUT committing. The process must then
      exit and restart to resume from the last commit: continuing to poll
      after a failed batch would let a later commit silently cover the gap.
    """

    def __init__(
        self,
        bootstrap_servers: str,
        group_id: str = "carma-trip-delays",
        topic: str = TRIP_UPDATES_TOPIC,
        batch_size: int = 500,
        poll_timeout_seconds: float = 1.0,
    ) -> None:
        self._consumer = Consumer(
            {
                "bootstrap.servers": bootstrap_servers,
                "group.id": group_id,
                "enable.auto.commit": False,
                # First run has no committed offset; start from the oldest
                # retained data instead of silently skipping to the tail.
                "auto.offset.reset": "earliest",
                "logger": _log,
            }
        )
        self._consumer.subscribe([topic])
        self._batch_size = batch_size
        self._poll_timeout_seconds = poll_timeout_seconds

    def process_batch(self, handler: Callable[[Sequence[TripDelay]], None]) -> int:
        """Drain one batch through the handler; messages consumed (incl. skipped).

        Returns 0 when the poll timed out with nothing to do.
        """
        messages = self._consumer.consume(self._batch_size, timeout=self._poll_timeout_seconds)
        delays: list[TripDelay] = []
        for message in messages:
            error = message.error()
            if error is not None:
                if error.retriable() or error.code() == KafkaError._PARTITION_EOF:
                    _log.warning("event=consume_transient_error error=%s", error)
                    continue
                raise KafkaException(error)
            try:
                delays.append(deserialize_trip_delay(message.value() or b""))
            except FeedDecodeError as exc:
                _log.warning(
                    "event=poison_message_skipped topic=%s partition=%s offset=%s error=%s",
                    message.topic(),
                    message.partition(),
                    message.offset(),
                    exc,
                )
        if delays:
            handler(delays)
        if messages:
            # consume() already advanced the local position past this batch
            # (poison included), so committing here commits exactly the batch.
            self._consumer.commit(asynchronous=False)
        return len(messages)

    def run(self, handler: Callable[[Sequence[TripDelay]], None], stop: threading.Event) -> None:
        while not stop.is_set():
            processed = self.process_batch(handler)
            if processed:
                _log.info("event=trip_updates_consumed messages=%d", processed)

    def close(self) -> None:
        self._consumer.close()
