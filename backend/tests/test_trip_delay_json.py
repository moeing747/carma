import json
from datetime import UTC, datetime

import pytest

from carma.adapters.trip_delay_json import (
    deserialize_trip_delay,
    message_key,
    serialize_trip_delay,
)
from carma.domain.errors import FeedDecodeError
from carma.domain.models import StopTimeEvent, TripDelay, TripId

DELAY = TripDelay(
    trip_id=TripId("trip-1"),
    route_id="27288_700",
    timestamp=datetime(2026, 7, 16, 12, 0, 30, tzinfo=UTC),
    stop_time_events=(
        StopTimeEvent(
            stop_id="S1", stop_sequence=1, arrival_delay_seconds=120, departure_delay_seconds=90
        ),
        StopTimeEvent(
            stop_id="S2", stop_sequence=2, arrival_delay_seconds=None, departure_delay_seconds=None
        ),
    ),
)


def test_round_trip_preserves_the_delay() -> None:
    assert deserialize_trip_delay(serialize_trip_delay(DELAY)) == DELAY


def test_message_key_is_route_and_trip() -> None:
    assert message_key(DELAY) == "27288_700:trip-1"


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff\x00 garbage bytes",
        b"not json at all",
        b"[]",
        b'{"trip_id": "t"}',
        b'{"trip_id": 7, "route_id": "r", "timestamp": "2026-07-16T12:00:00+00:00",'
        b' "stop_time_events": []}',
        # naive timestamp: rejected, snapshot ordering needs aware instants
        b'{"trip_id": "t", "route_id": "r", "timestamp": "2026-07-16T12:00:00",'
        b' "stop_time_events": []}',
        b'{"trip_id": "t", "route_id": "r", "timestamp": "2026-07-16T12:00:00+00:00",'
        b' "stop_time_events": [{"stop_id": "S1"}]}',
    ],
)
def test_malformed_payloads_raise_feed_decode_error(payload: bytes) -> None:
    with pytest.raises(FeedDecodeError):
        deserialize_trip_delay(payload)


def test_boolean_is_not_a_valid_stop_sequence() -> None:
    payload = json.dumps(
        {
            "trip_id": "t",
            "route_id": "r",
            "timestamp": "2026-07-16T12:00:00+00:00",
            "stop_time_events": [
                {
                    "stop_id": "S1",
                    "stop_sequence": True,
                    "arrival_delay_seconds": None,
                    "departure_delay_seconds": None,
                }
            ],
        }
    ).encode()
    with pytest.raises(FeedDecodeError):
        deserialize_trip_delay(payload)
