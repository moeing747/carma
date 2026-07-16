"""JSON wire format for TripDelay messages on the ``trip-updates`` topic.

Message value (UTF-8 JSON object):

    {"trip_id": str,
     "route_id": str,
     "timestamp": str,              # ISO 8601, timezone-aware
     "stop_time_events": [
        {"stop_id": str, "stop_sequence": int,
         "arrival_delay_seconds": int|null,
         "departure_delay_seconds": int|null}, ...]}

Message key: ``routeId:tripId`` so a route's trips stay on one partition and
per-trip ordering is preserved.

Deserialization is strict and raises FeedDecodeError on anything malformed:
the consumer treats that as a poison message (log, skip, commit).
"""

import json
from datetime import datetime

from carma.domain.errors import FeedDecodeError
from carma.domain.models import StopTimeEvent, TripDelay, TripId


def message_key(delay: TripDelay) -> str:
    return f"{delay.route_id}:{delay.trip_id.value}"


def serialize_trip_delay(delay: TripDelay) -> bytes:
    return json.dumps(
        {
            "trip_id": delay.trip_id.value,
            "route_id": delay.route_id,
            "timestamp": delay.timestamp.isoformat(),
            "stop_time_events": [
                {
                    "stop_id": event.stop_id,
                    "stop_sequence": event.stop_sequence,
                    "arrival_delay_seconds": event.arrival_delay_seconds,
                    "departure_delay_seconds": event.departure_delay_seconds,
                }
                for event in delay.stop_time_events
            ],
        },
        separators=(",", ":"),
    ).encode()


def deserialize_trip_delay(payload: bytes) -> TripDelay:
    try:
        raw = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FeedDecodeError("message value is not valid JSON") from exc
    try:
        timestamp = datetime.fromisoformat(_str(raw["timestamp"]))
        if timestamp.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        events = tuple(
            StopTimeEvent(
                stop_id=_str(event["stop_id"]),
                stop_sequence=_int(event["stop_sequence"]),
                arrival_delay_seconds=_optional_int(event["arrival_delay_seconds"]),
                departure_delay_seconds=_optional_int(event["departure_delay_seconds"]),
            )
            for event in raw["stop_time_events"]
        )
        return TripDelay(
            trip_id=TripId(_str(raw["trip_id"])),
            route_id=_str(raw["route_id"]),
            timestamp=timestamp,
            stop_time_events=events,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise FeedDecodeError(f"message value is not a TripDelay: {exc}") from exc


def _str(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"expected string, got {type(value).__name__}")
    return value


def _int(value: object) -> int:
    # bool is an int subclass; a boolean here is malformed data, not a count.
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"expected integer, got {type(value).__name__}")
    return value


def _optional_int(value: object) -> int | None:
    return None if value is None else _int(value)
