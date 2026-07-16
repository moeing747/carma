"""Where a vehicle is along its trip, derived from schedule plus delays.

The VBB feed publishes no GPS, so a vehicle's position is *defined* by this
module: the production SQL engine must implement exactly these semantics
(the equivalence test in tests/test_positions_integration.py holds it to
that), and this pure implementation is the reference.

Inputs and conventions
----------------------

- The trip's scheduled stops, ordered by stop_sequence. Times are seconds
  since service-day start and may exceed 86400 on trips crossing midnight
  (see carma.domain.service_days); the query instant uses the same scale, so
  midnight needs no special handling here. A missing arrival falls back to
  the departure and vice versa; stops with neither time carry no schedule
  information and are ignored.
- The latest realtime delay events for the trip (possibly none), matched to
  stops by stop_sequence.
- The query instant ``at_seconds``, seconds into the same service day.

Delay semantics (GTFS-RT StopTimeUpdate propagation)
----------------------------------------------------

A delay event applies to its own stop and *propagates* to every following
stop until the next stop that has its own event. Stops before the first
event get no adjustment. At the event's own stop, the arrival and departure
delays apply separately (falling back to each other when one is absent);
the value that propagates onward is the departure delay (arrival delay when
departure is absent).

Adjusted times need not be monotonic — a later event with a smaller delay
can pull a stop's time before its predecessor's. Times are clamped to be
non-decreasing in stop order (arrival then departure per stop): a vehicle
cannot arrive before it departed the previous stop. Reported delays are
derived from the *clamped* times, so they reflect what the position shows.

Position rule
-------------

At instant ``t`` against the adjusted, clamped times:

- before the first stop's arrival: parked at the first stop (trips appear
  at their origin, the map is never empty for an active trip);
- between a stop's arrival and departure: dwelling, pinned at that stop
  (fraction 0 on a degenerate stop→stop segment);
- between a departure and the next arrival: travelling, at the linear
  fraction of elapsed over segment duration;
- after the last stop's arrival: the trip is over — no position (None).

The reported delay is the clamped-minus-scheduled difference at the segment
target: the next stop's arrival while travelling, the stop's own departure
while pinned.

Projection onto geometry
------------------------

``derive_position`` maps the (segment, fraction) progress onto the trip's
shape: each stop is located as a fraction along the shape (clamped to be
monotonic in stop order — ST_LineLocatePoint can otherwise jump backwards
on self-approaching shapes), and the position interpolates between the two
stops' shape fractions. Trips without a shape interpolate on the straight
line between the two stops. Bearing is the direction of travel along the
line at the derived point; a shapeless pinned vehicle has none.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from carma.domain.geometry import (
    bearing_at_fraction,
    locate_fraction,
    point_at_fraction,
)
from carma.domain.models import Coordinate, ScheduledStop, StopTimeEvent


@dataclass(frozen=True, slots=True)
class EffectiveStopTime:
    """A stop's schedule with realtime adjustment applied and clamped."""

    stop: ScheduledStop
    scheduled_arrival: int
    scheduled_departure: int
    arrival_seconds: int
    departure_seconds: int


@dataclass(frozen=True, slots=True)
class TripProgress:
    """Where along its stop list a trip is: indexes into the effective-stop
    tuple, not raw stop_sequence values."""

    from_index: int
    to_index: int
    fraction: float
    delay_seconds: int

    @property
    def pinned(self) -> bool:
        return self.from_index == self.to_index


@dataclass(frozen=True, slots=True)
class DerivedPosition:
    coordinate: Coordinate
    bearing: float | None
    delay_seconds: int


def _adjustments(
    events: Sequence[StopTimeEvent], stop_sequence: int
) -> tuple[int, int]:
    """(arrival, departure) delay adjustment in force at a stop_sequence."""
    applicable = [event for event in events if event.stop_sequence <= stop_sequence]
    if not applicable:
        return 0, 0
    event = max(applicable, key=lambda item: item.stop_sequence)
    arrival = event.arrival_delay_seconds
    departure = event.departure_delay_seconds
    if event.stop_sequence == stop_sequence:
        return (
            arrival if arrival is not None else (departure or 0),
            departure if departure is not None else (arrival or 0),
        )
    propagated = departure if departure is not None else (arrival or 0)
    return propagated, propagated


def effective_stop_times(
    stops: Sequence[ScheduledStop], events: Sequence[StopTimeEvent]
) -> tuple[EffectiveStopTime, ...]:
    """Adjusted, clamped stop times; stops without any time are dropped."""
    result: list[EffectiveStopTime] = []
    running_max: int | None = None
    for stop in stops:
        arrival = (
            stop.arrival_seconds if stop.arrival_seconds is not None else stop.departure_seconds
        )
        departure = (
            stop.departure_seconds if stop.departure_seconds is not None else stop.arrival_seconds
        )
        if arrival is None or departure is None:
            continue
        arrival_adjust, departure_adjust = _adjustments(events, stop.stop_sequence)
        effective_arrival = arrival + arrival_adjust
        effective_departure = departure + departure_adjust
        clamped_arrival = (
            effective_arrival if running_max is None else max(effective_arrival, running_max)
        )
        clamped_departure = max(effective_departure, clamped_arrival)
        running_max = max(
            running_max if running_max is not None else effective_arrival,
            effective_arrival,
            effective_departure,
        )
        result.append(
            EffectiveStopTime(
                stop=stop,
                scheduled_arrival=arrival,
                scheduled_departure=departure,
                arrival_seconds=clamped_arrival,
                departure_seconds=clamped_departure,
            )
        )
    return tuple(result)


def progress_at(
    stops: Sequence[ScheduledStop],
    events: Sequence[StopTimeEvent],
    at_seconds: int,
) -> TripProgress | None:
    """The trip's progress at an instant; None once the trip has ended
    (or when the schedule carries no usable times at all)."""
    timed = effective_stop_times(stops, events)
    return _progress(timed, at_seconds)


def _progress(
    timed: Sequence[EffectiveStopTime], at_seconds: int
) -> TripProgress | None:
    for index, current in enumerate(timed):
        if at_seconds < current.arrival_seconds:
            if index == 0:
                return TripProgress(
                    from_index=0,
                    to_index=0,
                    fraction=0.0,
                    delay_seconds=current.departure_seconds - current.scheduled_departure,
                )
            previous = timed[index - 1]
            # Strictly positive: at_seconds lies strictly between the
            # previous clamped departure and this clamped arrival.
            duration = current.arrival_seconds - previous.departure_seconds
            return TripProgress(
                from_index=index - 1,
                to_index=index,
                fraction=(at_seconds - previous.departure_seconds) / duration,
                delay_seconds=current.arrival_seconds - current.scheduled_arrival,
            )
        if at_seconds <= current.departure_seconds:
            return TripProgress(
                from_index=index,
                to_index=index,
                fraction=0.0,
                delay_seconds=current.departure_seconds - current.scheduled_departure,
            )
    return None


def _monotonic_shape_fractions(
    shape: Sequence[Coordinate], timed: Sequence[EffectiveStopTime]
) -> list[float]:
    fractions: list[float] = []
    running = 0.0
    for entry in timed:
        running = max(running, locate_fraction(shape, entry.stop.coordinate))
        fractions.append(running)
    return fractions


def derive_position(
    stops: Sequence[ScheduledStop],
    events: Sequence[StopTimeEvent],
    shape: Sequence[Coordinate] | None,
    at_seconds: int,
) -> DerivedPosition | None:
    """The reference position derivation: progress projected onto geometry."""
    timed = effective_stop_times(stops, events)
    progress = _progress(timed, at_seconds)
    if progress is None:
        return None
    if shape is not None and len(shape) >= 2:
        fractions = _monotonic_shape_fractions(shape, timed)
        from_fraction = fractions[progress.from_index]
        to_fraction = fractions[progress.to_index]
        position_fraction = from_fraction + progress.fraction * (to_fraction - from_fraction)
        return DerivedPosition(
            coordinate=point_at_fraction(shape, position_fraction),
            bearing=bearing_at_fraction(shape, position_fraction),
            delay_seconds=progress.delay_seconds,
        )
    if progress.pinned:
        return DerivedPosition(
            coordinate=timed[progress.from_index].stop.coordinate,
            bearing=None,
            delay_seconds=progress.delay_seconds,
        )
    line = (
        timed[progress.from_index].stop.coordinate,
        timed[progress.to_index].stop.coordinate,
    )
    return DerivedPosition(
        coordinate=point_at_fraction(line, progress.fraction),
        bearing=bearing_at_fraction(line, progress.fraction),
        delay_seconds=progress.delay_seconds,
    )
