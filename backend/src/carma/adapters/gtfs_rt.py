from datetime import UTC, datetime

from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

from carma.domain.errors import FeedDecodeError
from carma.domain.models import StopTimeEvent, TripDelay, TripId


def decode_trip_updates(payload: bytes) -> list[TripDelay]:
    feed = gtfs_realtime_pb2.FeedMessage()
    try:
        feed.ParseFromString(payload)
    except DecodeError as exc:
        raise FeedDecodeError("payload is not a valid GTFS-RT FeedMessage") from exc

    delays: list[TripDelay] = []
    for entity in feed.entity:
        # VBB publishes TripUpdates only, but the spec allows mixed feeds;
        # entities of other kinds are skipped, not treated as errors.
        if not entity.HasField("trip_update"):
            continue
        trip_update = entity.trip_update
        events = tuple(
            StopTimeEvent(
                stop_id=stu.stop_id,
                stop_sequence=stu.stop_sequence,
                arrival_delay_seconds=stu.arrival.delay if stu.HasField("arrival") else None,
                departure_delay_seconds=stu.departure.delay if stu.HasField("departure") else None,
            )
            for stu in trip_update.stop_time_update
        )
        timestamp = trip_update.timestamp or feed.header.timestamp
        delays.append(
            TripDelay(
                trip_id=TripId(trip_update.trip.trip_id),
                route_id=trip_update.trip.route_id,
                timestamp=datetime.fromtimestamp(timestamp, tz=UTC),
                stop_time_events=events,
            )
        )
    return delays
