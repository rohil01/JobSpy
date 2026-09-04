"""jobsniffer.http.fixtures

On-disk format shared by ReplayClient (reads fixtures back in tests) and
src/scripts/har_to_fixtures.py (writes fixtures derived from HAR captures).
One JSON object per line (JSONL), so fixtures diff cleanly and can be
appended to incrementally during a live recording session.

Field choice mirrors the HAR format itself: response bodies are stored
base64-encoded regardless of content type (text or binary), which is exactly
how the project's own input/*.har captures store `response.content.text` --
reusing that convention means har_to_fixtures.py does a near-direct copy
rather than a format translation.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jobsniffer.http.exceptions import FixtureFileError

# Fixtures get committed to a public repo. Response headers carry no
# functional value for replay or parsing (parsers work off `.content`;
# `encoding` is tracked separately; fixture matching never keys on
# headers -- confirmed by reading ReplayClient._replay) but sites do
# return session/credential material in them (e.g. LinkedIn's anonymous
# jobs-guest search sets a fresh Set-Cookie: JSESSIONID=... on every
# request). Since nothing needs headers beyond a handful of genuinely
# useful ones, this is an ALLOW-list, not a deny-list: a deny-list only
# blocks header names someone thought to name in advance, and the next
# site could just as easily leak credentials under some other header name
# (X-Auth-Token, X-Amz-Security-Token, a vendor API key, ...). Applied in
# the one constructor every fixture-writing path (har_to_fixtures.py,
# ReplayClient's record mode) goes through, rather than trusting each
# caller to remember to filter.
_ALLOWED_HEADER_NAMES = frozenset({"content-type", "content-length", "content-encoding"})


def _sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() in _ALLOWED_HEADER_NAMES}


def compute_body_signature(*, json_body: Any = None, data: Any = None) -> str | None:
    """Stable signature for a request body, used to disambiguate fixtures
    that share a method+URL but differ by POST payload (GraphQL queries,
    Connect-RPC protobuf calls). Returns None when the request has no body,
    so GET fixtures aren't forced to carry a spurious signature."""
    if json_body is not None:
        raw = json.dumps(json_body, sort_keys=True, default=str).encode()
    elif isinstance(data, (bytes, bytearray)):
        raw = bytes(data)
    elif isinstance(data, str):
        raw = data.encode()
    elif isinstance(data, dict):
        raw = json.dumps(data, sort_keys=True, default=str).encode()
    else:
        return None
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class RecordedExchange:
    """One HTTP request/response pair, as replayed by ReplayClient."""

    method: str
    url: str
    status_code: int
    content_b64: str
    headers: dict[str, str] = field(default_factory=dict)
    encoding: str | None = "utf-8"
    request_body_signature: str | None = None

    @property
    def content(self) -> bytes:
        return base64.b64decode(self.content_b64)

    @classmethod
    def from_bytes(
        cls,
        *,
        method: str,
        url: str,
        status_code: int,
        content: bytes,
        headers: dict[str, str] | None = None,
        encoding: str | None = "utf-8",
        request_body_signature: str | None = None,
    ) -> RecordedExchange:
        return cls(
            method=method.upper(),
            url=url,
            status_code=status_code,
            content_b64=base64.b64encode(content).decode("ascii"),
            headers=_sanitize_headers(headers or {}),
            encoding=encoding,
            request_body_signature=request_body_signature,
        )

    def to_json_line(self) -> str:
        return json.dumps(
            {
                "method": self.method,
                "url": self.url,
                "status_code": self.status_code,
                "content_b64": self.content_b64,
                "headers": self.headers,
                "encoding": self.encoding,
                "request_body_signature": self.request_body_signature,
            }
        )

    @classmethod
    def from_json_line(cls, line: str) -> RecordedExchange:
        data = json.loads(line)
        return cls(
            method=data["method"],
            url=data["url"],
            status_code=data["status_code"],
            content_b64=data["content_b64"],
            headers=data.get("headers", {}),
            encoding=data.get("encoding", "utf-8"),
            request_body_signature=data.get("request_body_signature"),
        )


def load_fixtures(path: Path) -> list[RecordedExchange]:
    """Reads every recorded exchange from a JSONL fixture file.

    Raises FixtureFileError if the file is missing or a line is malformed --
    a broken fixture file is a setup bug, not something to skip past (P8).
    """
    if not path.is_file():
        raise FixtureFileError(f"Fixture file not found: {path}")
    exchanges: list[RecordedExchange] = []
    for line_num, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            exchanges.append(RecordedExchange.from_json_line(line))
        except (json.JSONDecodeError, KeyError) as exc:
            raise FixtureFileError(
                f"Malformed fixture at {path}:{line_num}: {exc}"
            ) from exc
    return exchanges


def _match_key(exchange: RecordedExchange) -> tuple[str, str, str | None]:
    return (exchange.method, exchange.url, exchange.request_body_signature)


def write_fixtures(path: Path, exchanges: list[RecordedExchange]) -> None:
    """Upserts multiple recorded exchanges in a single read-merge-write
    pass, creating the parent directory and file if needed.

    Upserts by (method, url, request_body_signature): re-running
    har_to_fixtures.py against the same HAR replaces existing exchanges
    with the same key rather than appending stale duplicates behind them.
    ReplayClient._replay returns the first matching exchange it finds, so
    a blind append would silently shadow a newer recording with an older
    one still sitting earlier in the file -- upserting makes "safe to run
    multiple times" actually true instead of merely asserted. Doing this
    once for a whole batch (rather than once per exchange) is what keeps
    har_to_fixtures.py's per-HAR-entry loop from paying a read-merge-write
    pass for every matching entry.

    A no-op for an empty `exchanges` list: never creates a file (or
    rewrites an existing one) when there's nothing to merge in.
    """
    if not exchanges:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_fixtures(path) if path.is_file() else []
    merged: dict[tuple[str, str, str | None], RecordedExchange] = {
        _match_key(e): e for e in existing
    }
    for exchange in exchanges:
        merged[_match_key(exchange)] = exchange
    with path.open("w") as f:
        for e in merged.values():
            f.write(e.to_json_line())
            f.write("\n")


def append_fixture(path: Path, exchange: RecordedExchange) -> None:
    """Upserts a single recorded exchange -- see write_fixtures for
    semantics. Convenience wrapper for callers that write one exchange at
    a time as it becomes available (ReplayClient's record mode, appending
    after each live request completes)."""
    write_fixtures(path, [exchange])
