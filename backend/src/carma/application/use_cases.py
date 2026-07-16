from collections.abc import Callable, Sequence
from dataclasses import dataclass

from carma.application.ports import (
    FeedSource,
    FeedStatusRepository,
    TripDelayRepository,
    TripUpdatePublisher,
)
from carma.domain.models import TripDelay


@dataclass(frozen=True, slots=True)
class IngestFeedSnapshot:
    source: FeedSource
    decode: Callable[[bytes], list[TripDelay]]
    publisher: TripUpdatePublisher

    def execute(self) -> int | None:
        """Fetch, decode, and publish one snapshot; the count published.

        None when the source reports the feed unchanged: nothing is
        published, which the poller logs distinctly from an empty snapshot.
        """
        payload = self.source.fetch()
        if payload is None:
            return None
        delays = self.decode(payload)
        for delay in delays:
            self.publisher.publish(delay)
        return len(delays)


@dataclass(frozen=True, slots=True)
class ApplyTripDelays:
    """Consumer side: persist a batch of TripDelays and advance feed status.

    save() is latest-wins, so applying overlapping or replayed batches is
    idempotent. The status snapshot timestamp is the newest feed timestamp in
    the batch (the repository guarantees it never regresses).
    """

    repository: TripDelayRepository
    feed_status: FeedStatusRepository

    def execute(self, delays: Sequence[TripDelay]) -> None:
        if not delays:
            return
        for delay in delays:
            self.repository.save(delay)
        self.feed_status.record_snapshot(
            snapshot_at=max(delay.timestamp for delay in delays),
            entity_count=len(delays),
        )
