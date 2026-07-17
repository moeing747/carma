"""Pure cursor rule for the position delta stream.

An SSE client tracks the newest row it has seen as a keyset cursor on
``(computed_at, trip_id)``; each poll asks for rows strictly after it in
that order. Pairing the timestamp with the trip id matters when one
projector tick writes more rows than the read limit: the leftover rows
share the tick's ``computed_at``, and a timestamp-only cursor would skip
them forever. The rule is pure so it can be unit-tested without a database
or a socket: the cursor only ever advances (a batch of older rows — a
replay, a clock quirk — must not rewind it), and an empty batch leaves it
untouched.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from carma.domain.models import VehiclePosition


@dataclass(frozen=True, slots=True, order=True)
class PositionCursor:
    """Keyset position in the (computed_at, trip_id) stream order."""

    computed_at: datetime
    trip_id: str


def advance_cursor(
    previous: PositionCursor | None, rows: Sequence[VehiclePosition]
) -> PositionCursor | None:
    newest = max(
        (PositionCursor(computed_at=row.computed_at, trip_id=row.trip_id.value) for row in rows),
        default=None,
    )
    if newest is None:
        return previous
    if previous is None or newest > previous:
        return newest
    return previous
