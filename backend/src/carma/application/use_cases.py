from collections.abc import Callable
from dataclasses import dataclass

from carma.application.ports import FeedSource, TripUpdatePublisher
from carma.domain.models import TripDelay


@dataclass(frozen=True, slots=True)
class IngestFeedSnapshot:
    source: FeedSource
    decode: Callable[[bytes], list[TripDelay]]
    publisher: TripUpdatePublisher

    def execute(self) -> int:
        delays = self.decode(self.source.fetch())
        for delay in delays:
            self.publisher.publish(delay)
        return len(delays)
