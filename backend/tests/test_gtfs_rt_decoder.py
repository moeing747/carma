from datetime import UTC
from pathlib import Path

import pytest

from carma.adapters.gtfs_rt import decode_trip_updates
from carma.domain.errors import FeedDecodeError

FIXTURE = Path(__file__).parent / "fixtures" / "vbb-tripupdates-sample.pb"


def test_decodes_vbb_sample_into_trip_delays() -> None:
    delays = decode_trip_updates(FIXTURE.read_bytes())

    assert len(delays) > 0
    for delay in delays:
        assert delay.trip_id.value
        assert delay.route_id
        assert delay.timestamp.tzinfo is UTC

    events = [event for delay in delays for event in delay.stop_time_events]
    assert events
    assert any(event.stop_id for event in events)
    assert any(event.arrival_delay_seconds is not None for event in events)


def test_malformed_payload_raises_feed_decode_error() -> None:
    with pytest.raises(FeedDecodeError):
        decode_trip_updates(b"\xffdefinitely not a FeedMessage\xff")
