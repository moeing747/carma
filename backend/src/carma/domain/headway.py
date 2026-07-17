"""Delay-aware headway re-spacing on one line (bus-bunching mitigation).

The common axis all vehicles of a line are compared on is *pattern time*:
how many seconds into its trip's schedule a vehicle effectively is, i.e.
``now - delay - first_departure``. A vehicle running 2 minutes late is
exactly where the schedule put it 2 minutes ago. For trips sharing a stop
pattern (one line, one direction) the pattern-time gap between two
consecutive vehicles equals their projected headway at any downstream
point, so evening out those gaps evens out headways — and the gaps are
already in seconds, no shape length needed.

Holding a vehicle for ``h`` seconds at its next stop moves it back by
``h`` on this axis: the gap to the vehicle ahead grows by ``h``, the gap
to the vehicle behind shrinks by ``h``. That linear structure is the whole
optimization model; the engines behind the OptimizationEngine port only
decide how the hold budget (0..MAX_HOLD_SECONDS per vehicle) is spent.

This module carries the value types the port exchanges and the pure math
(gaps, spread, plan assembly) both engines and their tests share.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from carma.domain.models import ScheduledStop, TripId

MAX_HOLD_SECONDS = 300
MIN_VEHICLES = 3


@dataclass(frozen=True, slots=True)
class LineVehicle:
    """One live vehicle on the line, located on the pattern-time axis."""

    trip_id: TripId
    position_seconds: float
    """Effective seconds into the trip's stop pattern; larger = further along."""
    delay_seconds: int
    next_stop_id: str
    next_stop_name: str


@dataclass(frozen=True, slots=True)
class HoldRecommendation:
    trip_id: TripId
    hold_seconds: int
    next_stop_id: str
    next_stop_name: str
    headway_before_seconds: float | None
    """Gap to the vehicle ahead before holds; None for the leader."""
    headway_after_seconds: float | None


@dataclass(frozen=True, slots=True)
class HeadwaySummary:
    vehicle_count: int
    headway_stddev_before_seconds: float
    headway_stddev_after_seconds: float


@dataclass(frozen=True, slots=True)
class HeadwayPlan:
    """Per-vehicle recommendations, leader first, plus the aggregate spread."""

    recommendations: tuple[HoldRecommendation, ...]
    summary: HeadwaySummary


def order_leader_first(vehicles: Sequence[LineVehicle]) -> tuple[LineVehicle, ...]:
    """Vehicles sorted by pattern position, furthest along first (ties by id
    so plans are deterministic)."""
    return tuple(
        sorted(vehicles, key=lambda v: (-v.position_seconds, v.trip_id.value))
    )


def gaps(positions: Sequence[float]) -> tuple[float, ...]:
    """Consecutive headways of leader-first positions; length n-1."""
    return tuple(
        positions[index] - positions[index + 1] for index in range(len(positions) - 1)
    )


def gaps_after_holds(positions: Sequence[float], holds: Sequence[int]) -> tuple[float, ...]:
    """Projected headways once each vehicle is held at its next stop."""
    if len(holds) != len(positions):
        raise ValueError("one hold per vehicle required")
    return gaps([position - hold for position, hold in zip(positions, holds, strict=True)])


def stddev(values: Sequence[float]) -> float:
    """Population standard deviation; 0.0 for fewer than two values."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def max_abs_deviation(values: Sequence[float]) -> float:
    """Largest |value - mean|; the min-max engines' objective, 0.0 if empty."""
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return max(abs(value - mean) for value in values)


def build_plan(vehicles: Sequence[LineVehicle], holds: Sequence[int]) -> HeadwayPlan:
    """Assemble the advisory plan for leader-first vehicles and their holds.

    Safety net shared by every engine: a hold vector that would *worsen*
    the headway spread collapses to the zero plan — advice must never be
    worse than doing nothing.
    """
    if len(holds) != len(vehicles):
        raise ValueError("one hold per vehicle required")
    positions = [vehicle.position_seconds for vehicle in vehicles]
    before = gaps(positions)
    after = gaps_after_holds(positions, holds)
    if stddev(after) > stddev(before):
        holds = [0] * len(vehicles)
        after = before
    recommendations = tuple(
        HoldRecommendation(
            trip_id=vehicle.trip_id,
            hold_seconds=hold,
            next_stop_id=vehicle.next_stop_id,
            next_stop_name=vehicle.next_stop_name,
            headway_before_seconds=before[index - 1] if index > 0 else None,
            headway_after_seconds=after[index - 1] if index > 0 else None,
        )
        for index, (vehicle, hold) in enumerate(zip(vehicles, holds, strict=True))
    )
    return HeadwayPlan(
        recommendations=recommendations,
        summary=HeadwaySummary(
            vehicle_count=len(vehicles),
            headway_stddev_before_seconds=stddev(before),
            headway_stddev_after_seconds=stddev(after),
        ),
    )


def pattern_position(
    stops: Sequence[ScheduledStop], delay_seconds: int, seconds_of_day: int
) -> tuple[float, ScheduledStop] | None:
    """(pattern seconds, next stop) for a vehicle, or None without a schedule.

    ``seconds_of_day`` is feed-local wall time in 0..86400; GTFS times may
    exceed 86400 on trips crossing midnight, so the instant is compared as
    whichever of ``t`` / ``t + 86400`` lies closest to the trip's span (the
    same convention as carma.domain.service_days). The next stop is the
    first whose scheduled arrival the vehicle has not yet effectively
    reached; a vehicle past its last stop holds nowhere, so the last stop
    is returned and the position clamps to the pattern's end.
    """
    timed = [
        stop
        for stop in stops
        if stop.arrival_seconds is not None or stop.departure_seconds is not None
    ]
    if not timed:
        return None
    first = timed[0]
    last = timed[-1]
    start = first.departure_seconds or first.arrival_seconds or 0
    end = last.arrival_seconds or last.departure_seconds or start
    now = min(
        (seconds_of_day, seconds_of_day + 86400),
        key=lambda candidate: max(start - candidate, candidate - end, 0),
    )
    effective = now - delay_seconds
    position = float(min(max(effective - start, 0), max(end - start, 0)))
    next_stop = next(
        (
            stop
            for stop in timed
            if (stop.arrival_seconds or stop.departure_seconds or 0) > effective
        ),
        last,
    )
    return position, next_stop
