"""Pure timing rules for the feed poll loop.

Kept free of clocks and sleeps so the schedule is unit-testable; the
entrypoint owns time.monotonic() and the actual waiting.
"""

from dataclasses import dataclass

_DEFAULT_MAX_BACKOFF_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class PollSchedule:
    interval_seconds: float
    max_backoff_seconds: float = _DEFAULT_MAX_BACKOFF_SECONDS

    def next_delay(self, elapsed_seconds: float, consecutive_failures: int) -> float:
        """Seconds to wait before the next poll.

        Steady state targets a fixed cadence: the time the poll itself took is
        deducted from the interval (never below zero). After failures the
        delay backs off exponentially from the interval, capped so a long
        upstream outage keeps probing at a bounded rate.
        """
        if consecutive_failures > 0:
            backoff = self.interval_seconds * (2.0 ** (consecutive_failures - 1))
            return min(backoff, self.max_backoff_seconds)
        return max(0.0, self.interval_seconds - elapsed_seconds)
