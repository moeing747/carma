from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from carma.application.ports import (
    FeedSource,
    FeedStatusRepository,
    PositionRecomputeEngine,
    TripDelayRepository,
    TripScheduleRepository,
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


@dataclass(frozen=True, slots=True)
class RecomputeReport:
    active_trips: int
    positions_written: int


@dataclass(frozen=True, slots=True)
class RecomputePositions:
    """One projection tick: resolve the active trips, hand them to the
    set-based engine, report the counts the worker logs."""

    schedule: TripScheduleRepository
    engine: PositionRecomputeEngine

    def execute(self, at: datetime) -> RecomputeReport:
        trip_ids = self.schedule.active_trip_ids(at)
        written = self.engine.recompute(trip_ids, at)
        return RecomputeReport(active_trips=len(trip_ids), positions_written=written)
