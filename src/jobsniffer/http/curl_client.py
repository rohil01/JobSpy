"""jobsniffer.http.curl_client

Production HTTP client. Uses curl_cffi rather than httpx (this org's default
per references/python-best-practices.md Sec. 7.4-adjacent guidance) because
impersonation is a hard requirement here, not a preference: curl_cffi
replicates a real browser's TLS/JA3 fingerprint at the libcurl layer, which
httpx (an ordinary Python TLS stack) cannot do. Every site jobsniffer targets
fingerprints inbound connections to distinguish real browsers from bots --
matching a Chrome-shaped signature is the entire reason this dependency
exists (see docs/2026-07-27-jobsniffer-modernization-plan.md, Phase 1).
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any, Self, cast

import stamina
from curl_cffi import requests as curl_requests
from curl_cffi.const import CurlOpt
from curl_cffi.requests.exceptions import RequestException
from curl_cffi.requests.session import HttpMethod

from jobsniffer.http.exceptions import HttpClientUnreachableError
from jobsniffer.http.proxy import ProxyRotator
from jobsniffer.logging_config import create_logger

log = create_logger("HttpClient")

# Default retryable set: 429 is deliberately excluded. Every existing
# scraper that cares about rate limiting (glassdoor, linkedin, ziprecruiter)
# already branches on `response.status_code == 429` itself. Auto-raising
# here would turn that inspectable response into an uncaught exception for
# callers that don't wrap their request in a try/except -- a real
# regression, not a deferred one. Only 5xx (unambiguously transient server
# failures) gets the transport-level retry-then-raise treatment by default.
# Configurable per-instance (not hardcoded) precisely because getting this
# set wrong once already broke 429 handling -- a future site-specific
# quirk shouldn't require another edit to this shared module.
_DEFAULT_RETRYABLE_STATUS = frozenset({500, 502, 503, 504})


class CurlCffiClient:
    """HttpClient backed by curl_cffi, impersonating a real Chrome build.

    Retries transient 5xx/connection failures with jittered exponential
    backoff via stamina; a request that exhausts retries raises the
    underlying curl_cffi exception rather than returning a fabricated
    response (P8 -- explicit failure propagation). 429 is returned to the
    caller as a normal response by default -- see the retryable_status
    parameter and _DEFAULT_RETRYABLE_STATUS comment.
    """

    def __init__(
        self,
        *,
        impersonate: str = "chrome",
        proxies: list[str] | str | None = None,
        ca_cert: str | None = None,
        verify: bool = True,
        timeout: float = 30.0,
        max_attempts: int = 3,
        retry_timeout: float = 60.0,
        wait_initial: float = 0.5,
        retryable_status: frozenset[int] = _DEFAULT_RETRYABLE_STATUS,
        session: curl_requests.Session | None = None,
    ) -> None:
        """`session` is an injection seam for tests: passing a fake session
        avoids paying for a real libcurl handle + TLS/JA3 setup just to
        immediately discard it, which every unit test in tests/http/
        would otherwise do."""
        self._proxy_rotator = ProxyRotator(proxies)
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._retry_timeout = retry_timeout
        self._wait_initial = wait_initial
        self._retryable_status = retryable_status

        if session is not None:
            self._session = session
        else:
            session_kwargs: dict[str, Any] = {
                "impersonate": impersonate,
                "verify": verify,
            }
            if ca_cert:
                session_kwargs["curl_options"] = {CurlOpt.CAINFO: ca_cert}
            self._session = curl_requests.Session(**session_kwargs)

    @property
    def headers(self) -> MutableMapping[str, str]:
        """Persistent session-level headers, matching requests.Session's
        convention. Several scrapers (glassdoor, linkedin, naukri, bdjobs,
        ziprecruiter) call `self.session.headers.update(...)` once after
        construction -- this passthrough is required for those call sites
        to keep working unchanged. curl_cffi's Headers is a MutableMapping
        subclass at runtime, matching HttpClient.headers in protocol.py --
        the cast below is only needed because curl_cffi's own type stub
        for Headers.__getitem__ returns `str | None`, stricter than the
        MutableMapping[str, str] contract requires."""
        return cast(MutableMapping[str, str], self._session.headers)

    def request(self, method: str, url: str, **kwargs: Any) -> curl_requests.Response:
        kwargs.setdefault("timeout", self._timeout)
        proxy = self._proxy_rotator.next()
        if proxy is not None:
            kwargs.setdefault("proxies", proxy)

        for attempt in stamina.retry_context(
            on=RequestException,
            attempts=self._max_attempts,
            timeout=self._retry_timeout,
            wait_initial=self._wait_initial,
            wait_max=15.0,
            wait_jitter=1.0,
            wait_exp_base=2.0,
        ):
            with attempt:
                # Our own HttpClient protocol deliberately types `method` as
                # `str` (it's not curl_cffi-specific); narrow only at the
                # boundary where curl_cffi's stricter Literal is required.
                response = self._session.request(
                    cast(HttpMethod, method), url, **kwargs
                )
                if response.status_code in self._retryable_status:
                    log.warning(
                        f"http.retryable_status method={method} url={url} "
                        f"status_code={response.status_code}"
                    )
                    response.raise_for_status()
                return response

        # stamina.retry_context always either returns from within `with attempt`
        # or raises once attempts/timeout are exhausted; this line is defensive,
        # not a real code path (P6: no silent fallthrough).
        raise HttpClientUnreachableError(method, url)  # pragma: no cover

    def get(self, url: str, **kwargs: Any) -> curl_requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> curl_requests.Response:
        return self.request("POST", url, **kwargs)

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
