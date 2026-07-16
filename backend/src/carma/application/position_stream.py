"""Pure cursor rule for the position delta stream.

An SSE client tracks the newest ``computed_at`` it has seen; each poll asks
for rows strictly newer than that cursor. The rule is pure so it can be
unit-tested without a database or a socket: the cursor only ever advances
(a batch of older rows — a replay, a clock quirk — must not rewind it), and
an empty batch leaves it untouched.
"""

from collections.abc import Sequence
from datetime import datetime

from carma.domain.models import VehiclePosition


def advance_cursor(
    previous: datetime | None, rows: Sequence[VehiclePosition]
) -> datetime | None:
    newest = max((row.computed_at for row in rows), default=None)
    if newest is None:
        return previous
    if previous is None or newest > previous:
        return newest
    return previous
