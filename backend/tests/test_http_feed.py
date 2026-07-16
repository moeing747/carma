"""HttpFeedSource against a local stdlib HTTP server (no network)."""

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from carma.adapters.http_feed import HttpFeedSource

BODY_V1 = b"feed-payload-v1"
ETAG_V1 = 'W/"v1"'


class _FeedHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.headers.get("If-None-Match") == ETAG_V1:
            self.send_response(304)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("ETag", ETAG_V1)
        self.send_header("Content-Length", str(len(BODY_V1)))
        self.end_headers()
        self.wfile.write(BODY_V1)

    def log_message(self, format: str, *args: object) -> None:
        pass  # keep pytest output clean


@pytest.fixture()
def feed_url() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), _FeedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/data"
    finally:
        server.shutdown()
        thread.join()


def test_first_fetch_downloads_the_payload(feed_url: str) -> None:
    source = HttpFeedSource(feed_url)

    assert source.fetch() == BODY_V1


def test_unchanged_feed_returns_none_via_etag(feed_url: str) -> None:
    source = HttpFeedSource(feed_url)

    assert source.fetch() == BODY_V1
    # Second conditional request hits the 304 path.
    assert source.fetch() is None


def test_sources_do_not_share_etag_state(feed_url: str) -> None:
    assert HttpFeedSource(feed_url).fetch() == BODY_V1
    assert HttpFeedSource(feed_url).fetch() == BODY_V1
