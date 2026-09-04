"""jobsniffer.http.proxy

Round-robins outbound proxies across requests. This is the same rotation
behavior as upstream JobSpy's RotatingProxySession, extracted so it composes
with any HttpClient implementation instead of being mixed into a
requests.Session/tls_client.Session subclass.
"""

from __future__ import annotations

from collections.abc import Iterator
from itertools import cycle


def _format_proxy(proxy: str) -> dict[str, str]:
    if proxy.startswith(("http://", "https://", "socks5://")):
        return {"http": proxy, "https": proxy}
    return {"http": f"http://{proxy}", "https": f"http://{proxy}"}


class ProxyRotator:
    """Cycles through a configured list of proxies, one per request.

    A bare "localhost" entry means "no proxy this turn" -- matches upstream
    JobSpy's convention of mixing direct connections into a proxy pool.
    """

    def __init__(self, proxies: list[str] | str | None = None) -> None:
        self._cycle: Iterator[dict[str, str]] | None = None
        if isinstance(proxies, str):
            self._cycle = cycle([_format_proxy(proxies)])
        elif proxies:
            self._cycle = cycle([_format_proxy(p) for p in proxies])

    def next(self) -> dict[str, str] | None:
        """Returns the next proxy dict, or None if no proxy should be used."""
        if self._cycle is None:
            return None
        candidate = next(self._cycle)
        if candidate["http"] == "http://localhost":
            return None
        return candidate
