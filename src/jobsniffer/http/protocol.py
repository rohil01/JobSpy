"""jobsniffer.http.protocol

Structural interfaces for jobsniffer's HTTP layer. These are the exact
subset of the requests-style API the scrapers actually call: get/post/
request/headers, plus a response with status_code/ok/text/content/
headers/json().

curl_cffi.requests.Response and .Session satisfy these Protocols already
(curl_cffi mirrors the `requests` surface closely), so CurlCffiClient can
delegate to curl_cffi directly instead of wrapping every attribute.
ReplayClient's RecordedResponse satisfies the same shape without inheriting
from anything curl_cffi-specific, which is what lets tests swap one
implementation for the other with no change to scraper code.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any, Protocol


class HttpResponse(Protocol):
    status_code: int
    ok: bool
    text: str
    content: bytes
    url: str
    headers: MutableMapping[str, str]

    def json(self) -> Any: ...


class HttpClient(Protocol):
    """Implementations: CurlCffiClient (production, TLS/JA3 impersonation)
    and ReplayClient (tests, replays or records HAR-derived fixtures).

    `headers` is a session-level, mutable mapping that several scrapers
    (glassdoor, linkedin, naukri, bdjobs, ziprecruiter) update once after
    construction -- e.g. `self.session.headers.update(auth_headers)` -- and
    expect every subsequent request to carry.
    """

    headers: MutableMapping[str, str]

    def get(self, url: str, **kwargs: Any) -> HttpResponse: ...

    def post(self, url: str, **kwargs: Any) -> HttpResponse: ...

    def request(self, method: str, url: str, **kwargs: Any) -> HttpResponse: ...

    def close(self) -> None: ...
