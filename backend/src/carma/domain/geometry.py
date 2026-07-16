"""Polyline geometry mirroring the PostGIS calls the SQL position engine uses.

The production engine derives positions in PostGIS; these functions are the
pure reference the equivalence tests hold it against, so each one mirrors a
specific PostGIS function's semantics rather than "correct" geodesy:

- ``locate_fraction``   ~ ``ST_LineLocatePoint``: closest point on the line,
  as a fraction of the line's 2D length — computed planar in raw degree
  space, exactly as PostGIS does for a geometry in SRID 4326.
- ``point_at_fraction`` ~ ``ST_LineInterpolatePoint``: the point at a
  fraction of the 2D degree-space length.
- ``initial_bearing``   ~ ``ST_Azimuth`` on geography: compass direction of
  travel. The pure version uses the spherical initial-bearing formula; over
  the ~meters epsilon the engine samples, it agrees with the spheroidal
  PostGIS value to well under a degree.

Planar math in degree space distorts distances (a degree of longitude at
Berlin is ~0.6 of a degree of latitude), but both implementations distort
identically, which is what equivalence requires. Positions stay on the shape
by construction either way.
"""

import math
from collections.abc import Sequence
from itertools import pairwise

from carma.domain.models import Coordinate

# Fraction of the line length over which the bearing is sampled; mirrors the
# epsilon the SQL engine feeds to ST_Azimuth.
BEARING_SAMPLE_FRACTION = 0.001


def _segment_lengths(line: Sequence[Coordinate]) -> list[float]:
    return [math.hypot(b.lon - a.lon, b.lat - a.lat) for a, b in pairwise(line)]


def locate_fraction(line: Sequence[Coordinate], point: Coordinate) -> float:
    """Fraction along ``line`` of the point on it closest to ``point``."""
    if len(line) < 2:
        return 0.0
    lengths = _segment_lengths(line)
    total = sum(lengths)
    if total == 0.0:
        return 0.0
    best_distance2 = math.inf
    best_travelled = 0.0
    travelled = 0.0
    for (a, b), length in zip(pairwise(line), lengths, strict=True):
        dx, dy = b.lon - a.lon, b.lat - a.lat
        if length == 0.0:
            t = 0.0
        else:
            t = ((point.lon - a.lon) * dx + (point.lat - a.lat) * dy) / (length * length)
            t = min(1.0, max(0.0, t))
        nx, ny = a.lon + t * dx, a.lat + t * dy
        distance2 = (point.lon - nx) ** 2 + (point.lat - ny) ** 2
        if distance2 < best_distance2:
            best_distance2 = distance2
            best_travelled = travelled + t * length
        travelled += length
    return best_travelled / total


def point_at_fraction(line: Sequence[Coordinate], fraction: float) -> Coordinate:
    """The point at ``fraction`` of the line's 2D length (clamped to [0, 1])."""
    if not line:
        raise ValueError("cannot interpolate on an empty line")
    if len(line) == 1:
        return line[0]
    lengths = _segment_lengths(line)
    total = sum(lengths)
    if total == 0.0:
        return line[0]
    target = min(1.0, max(0.0, fraction)) * total
    travelled = 0.0
    for (a, b), length in zip(pairwise(line), lengths, strict=True):
        if travelled + length >= target and length > 0.0:
            t = (target - travelled) / length
            return Coordinate(lat=a.lat + t * (b.lat - a.lat), lon=a.lon + t * (b.lon - a.lon))
        travelled += length
    return line[-1]


def initial_bearing(a: Coordinate, b: Coordinate) -> float | None:
    """Great-circle initial bearing from ``a`` to ``b`` in [0, 360) degrees.

    None for coincident points (a vehicle standing still has no direction),
    matching ST_Azimuth returning NULL for equal points.
    """
    if a == b:
        return None
    phi1, phi2 = math.radians(a.lat), math.radians(b.lat)
    delta = math.radians(b.lon - a.lon)
    y = math.sin(delta) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(delta)
    return math.degrees(math.atan2(y, x)) % 360.0


def bearing_at_fraction(line: Sequence[Coordinate], fraction: float) -> float | None:
    """Direction of travel along ``line`` at ``fraction``.

    Sampled over a small forward window (shifted back at the line's end so
    the window stays inside the line), mirroring the SQL engine's ST_Azimuth
    over the same epsilon. None when the window degenerates to a point.
    """
    if len(line) < 2:
        return None
    start = min(max(fraction, 0.0), 1.0 - BEARING_SAMPLE_FRACTION)
    start = max(start, 0.0)
    end = min(start + BEARING_SAMPLE_FRACTION, 1.0)
    return initial_bearing(point_at_fraction(line, start), point_at_fraction(line, end))
