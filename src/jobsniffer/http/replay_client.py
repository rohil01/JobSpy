"""jobsniffer.http.replay_client

Test-time HttpClient. Two modes:

- "replay" (default): serves recorded exchanges from a JSONL fixture file.
  Matching is (method, full URL with any `params` merged in, request body
  signature) -- see jobsniffer.http.fixtures.compute_body_signature. A
  request with no matching fixture raises FixtureNotFoundError; it never
  falls back to an empty/fabricated response (P8).
- "record": wraps a real CurlCffiClient, makes the live request, and appends
  the resulting exchange to the fixture file as a side effect. Used once,
  interactively, to seed fixtures for sites with no HAR ground truth
  (LinkedIn's guest API, the five unverified scrapers) -- see
  docs/2026-07-27-jobsniffer-modernization-plan.md, Phase 2.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode, urlparse, urlunparse

from jobsniffer.http.curl_client import CurlCffiClient
from jobsniffer.http.exceptions import FixtureNotFoundError
from jobsniffer.http.fixtures import (
    RecordedExchange,
    append_fixture,
    compute_body_signature,
    load_fixtures,
)


def _merge_url_params(url: str, params: dict[str, Any] | None) -> str:
    """Folds a `params=` kwarg into the URL's query string, the same way a
    real HTTP client would before it hits the wire -- so replay matching
    keys on the URL the server actually saw, not the caller's shorthand."""
    if not params:
        return url
    parsed = urlparse(url)
    extra = urlencode(params, doseq=True)
    query = f"{parsed.query}&{extra}" if parsed.query else extra
    return urlunparse(parsed._replace(query=query))


class RecordedResponse:
    """Satisfies jobsniffer.http.protocol.HttpResponse by wrapping a single
    RecordedExchange. Deliberately not a dataclass: `.json()` needs to be a
    method (matching curl_cffi's Response), not a field."""

    def __init__(self, exchange: RecordedExchange) -> None:
        self._exchange = exchange
        self._text: str | None = None
        self.status_code = exchange.status_code
        self.ok = 200 <= exchange.status_code < 400
        self.headers = exchange.headers
        self.url = exchange.url
        self.content = exchange.content

    @property
    def text(self) -> str:
        """Lazy and memoized, matching curl_cffi.requests.Response.text
        exactly (verified against its source): decoding only happens on
        first access, using errors="replace" rather than raising, and the
        result is cached so repeated .text/.json() calls on the same
        response don't redecode the body each time. Laziness matters
        because binary fixtures (e.g. ZipRecruiter's protobuf detail
        responses) are never valid UTF-8 -- constructing a RecordedResponse
        for one must not crash before the caller even gets a chance to read
        `.content` instead of `.text`, the same way a real CurlCffiClient
        wouldn't."""
        if self._text is None:
            try:
                self._text = self.content.decode(
                    self._exchange.encoding or "utf-8", errors="replace"
                )
            except LookupError:
                self._text = self.content.decode("utf-8", errors="replace")
        return self._text

    def json(self) -> Any:
        return json.loads(self.text)


class ReplayClient:
    """HttpClient implementation for tests. See module docstring for modes."""

    def __init__(
        self,
        fixture_path: Path,
        *,
        mode: Literal["replay", "record"] = "replay",
        recorder: CurlCffiClient | None = None,
    ) -> None:
        self._fixture_path = fixture_path
        self._mode = mode
        self._recorder = recorder
        self._exchanges: list[RecordedExchange] = (
            load_fixtures(fixture_path) if mode == "replay" else []
        )
        # Session-level headers, matching HttpClient.headers -- this exists
        # so `self.session.headers.update(...)` call sites (glassdoor,
        # linkedin, naukri, bdjobs, ziprecruiter) don't raise AttributeError
        # when a scraper is exercised against a ReplayClient in tests.
        #
        # KNOWN, INTENTIONAL LIMITATION: this is write-only. Fixture
        # matching keys on method/url/body signature only, never on
        # headers, so a scraper that sets a wrong or missing header will
        # replay identically to one that sets it correctly. No scraper
        # currently reads a header value back out of `.headers` (only
        # Indeed's separate, unrelated `.headers` dict does per-request
        # lookups), so this hasn't mattered yet -- but a header-driven
        # auth/locale bug is a class of regression this Protocol cannot
        # catch in replay mode. If that ever needs testing, headers would
        # need to join the match key (or be recorded on RecordedExchange
        # for assertion), not silently discarded as they are today.
        self.headers: dict[str, str] = {}

        if mode == "record" and recorder is None:
            self._recorder = CurlCffiClient()

    def request(self, method: str, url: str, **kwargs: Any) -> RecordedResponse:
        full_url = _merge_url_params(url, kwargs.get("params"))
        body_sig = compute_body_signature(
            json_body=kwargs.get("json"), data=kwargs.get("data")
        )

        if self._mode == "record":
            return self._record(method, full_url, body_sig, **kwargs)
        return self._replay(method, full_url, body_sig)

    def _replay(
        self, method: str, full_url: str, body_sig: str | None
    ) -> RecordedResponse:
        method_upper = method.upper()
        for exchange in self._exchanges:
            if (
                exchange.method == method_upper
                and exchange.url == full_url
                and exchange.request_body_signature == body_sig
            ):
                return RecordedResponse(exchange)
        raise FixtureNotFoundError(method_upper, full_url)

    def _record(
        self, method: str, full_url: str, body_sig: str | None, **kwargs: Any
    ) -> RecordedResponse:
        # A record-mode client always routes here (see request() above) and
        # never reads self._exchanges back -- that list is only populated
        # and consulted in "replay" mode, so recording doesn't maintain a
        # second, never-read copy of every exchange in memory.
        assert self._recorder is not None  # set in __init__ for mode="record"
        live_response = self._recorder.request(method, full_url, **kwargs)
        exchange = RecordedExchange.from_bytes(
            method=method,
            url=full_url,
            status_code=live_response.status_code,
            content=live_response.content,
            headers={k: v for k, v in live_response.headers.items() if v is not None},
            request_body_signature=body_sig,
        )
        append_fixture(self._fixture_path, exchange)
        return RecordedResponse(exchange)

    def get(self, url: str, **kwargs: Any) -> RecordedResponse:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> RecordedResponse:
        return self.request("POST", url, **kwargs)

    def close(self) -> None:
        if self._recorder is not None:
            self._recorder.close()
