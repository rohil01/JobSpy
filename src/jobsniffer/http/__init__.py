"""jobsniffer.http

HttpClient is the seam between scrapers and the network. Scrapers only ever
call .get()/.post()/.request() and read .status_code/.ok/.text/.content/
.json() off the result -- they never import curl_cffi (or requests) or
ReplayClient directly. That's what lets tests swap CurlCffiClient for
ReplayClient without touching scraper code.
"""

from __future__ import annotations

from jobsniffer.http.curl_client import CurlCffiClient
from jobsniffer.http.exceptions import (
    FixtureFileError,
    FixtureNotFoundError,
    HttpClientError,
    HttpClientUnreachableError,
)
from jobsniffer.http.fixtures import RecordedExchange, compute_body_signature
from jobsniffer.http.protocol import HttpClient, HttpResponse
from jobsniffer.http.replay_client import RecordedResponse, ReplayClient

__all__ = [
    "CurlCffiClient",
    "FixtureFileError",
    "FixtureNotFoundError",
    "HttpClient",
    "HttpClientError",
    "HttpClientUnreachableError",
    "HttpResponse",
    "RecordedExchange",
    "RecordedResponse",
    "ReplayClient",
    "compute_body_signature",
]
