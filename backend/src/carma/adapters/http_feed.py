"""HTTP FeedSource with conditional requests.

stdlib urllib on purpose: the poller issues one plain GET every ~30 seconds
against a single URL -- no pooling, retries, or async that would justify a
new runtime dependency (httpx/requests).
"""

import urllib.error
import urllib.request

_USER_AGENT = "carma/0.1 (+https://github.com/moeing747)"


class HttpFeedSource:
    """FeedSource port over HTTP GET.

    Remembers the last ETag and sends If-None-Match (the VBB endpoint serves
    ETags); a 304 response returns None ("unchanged"), so identical snapshots
    are neither downloaded nor republished.
    """

    def __init__(self, url: str, timeout_seconds: float = 30.0) -> None:
        self._url = url
        self._timeout_seconds = timeout_seconds
        self._etag: str | None = None

    def fetch(self) -> bytes | None:
        request = urllib.request.Request(self._url, headers={"User-Agent": _USER_AGENT})
        if self._etag is not None:
            request.add_header("If-None-Match", self._etag)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                self._etag = response.headers.get("ETag")
                payload: bytes = response.read()
                return payload
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                return None
            raise
