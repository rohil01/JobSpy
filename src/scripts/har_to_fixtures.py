"""src.scripts.har_to_fixtures

Converts a HAR capture into jobsniffer's JSONL fixture format
(jobsniffer.http.fixtures.RecordedExchange), so scraper parsers can be unit
tested against real recorded response bodies without touching the network.

Reusable utility per this project's P2 (no one-off scripts): the extraction
logic lives in extract_fixtures_from_har(), which src/scripts/*.py callers
and tests/scripts/test_har_to_fixtures.py both exercise directly. A thin
argparse CLI wraps it for interactive use.

Usage:
    uv run python -m scripts.har_to_fixtures \\
        --har /path/to/www.indeed.com.har \\
        --url-contains "indeed.com/jobs?" --url-contains "/viewjob" \\
        --out tests/fixtures/indeed.jsonl

HAR quirk this handles: response bodies are base64-encoded whenever
`response.content.encoding == "base64"` (true for every capture in this
project's input/*.har -- confirmed by inspecting them directly). Missing
this silently yields empty/garbled fixtures, which is exactly the kind of
mistake this script exists to make impossible to repeat.
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

from jobsniffer.http.fixtures import (
    RecordedExchange,
    compute_body_signature,
    write_fixtures,
)

_TEXTUAL_MIME_MARKERS = ("json", "html", "text", "xml", "javascript")


def _is_textual(mime_type: str) -> bool:
    mime_lower = mime_type.lower()
    return any(marker in mime_lower for marker in _TEXTUAL_MIME_MARKERS)


def _decode_response_body(content: dict) -> bytes | None:
    """Returns the raw response body bytes, or None if no body was captured
    (some HAR entries have an empty/absent `content.text` -- e.g. redirects,
    204s, or requests the capture tool didn't record a body for)."""
    text = content.get("text")
    if not text:
        return None
    if content.get("encoding") == "base64":
        return base64.b64decode(text)
    return text.encode("utf-8")


def _request_body_signature(request: dict) -> str | None:
    """Best-effort signature from HAR's captured request.postData.text.

    HAR does not reliably distinguish binary from textual POST bodies the
    way it does for responses (no `encoding` field on postData in any
    capture inspected for this project), so a binary POST body's HAR text
    representation may not byte-for-byte match what compute_body_signature
    would compute from the same request sent live. This is acceptable here:
    these fixtures are read via load_fixtures() for direct parser testing,
    not served through ReplayClient's request-matching replay path, so an
    imprecise signature costs nothing in that usage.
    """
    post_data = request.get("postData")
    if not post_data or not post_data.get("text"):
        return None
    return compute_body_signature(data=post_data["text"])


def extract_fixtures_from_har(
    har_path: Path, url_contains: list[str], out_path: Path
) -> int:
    """Reads har_path, upserts one RecordedExchange per matching entry with
    a captured response body into out_path (JSONL -- safe to call multiple
    times/for multiple HARs feeding the same fixture file; a later call
    replaces same-key exchanges rather than appending stale duplicates
    behind them, see jobsniffer.http.fixtures.write_fixtures).

    An entry matches if its request URL contains ANY of the url_contains
    substrings. All matches are collected and written in a single pass
    (not one file rewrite per HAR entry) -- returns the number written.
    """
    har = json.loads(har_path.read_text())
    exchanges = []
    for entry in har["log"]["entries"]:
        request = entry["request"]
        url = request["url"]
        if not any(needle in url for needle in url_contains):
            continue

        response = entry["response"]
        content = response.get("content", {})
        body = _decode_response_body(content)
        if body is None:
            continue

        mime_type = content.get("mimeType", "")
        response_headers = {
            h["name"]: h["value"] for h in response.get("headers", [])
        }
        exchanges.append(
            RecordedExchange.from_bytes(
                method=request["method"],
                url=url,
                status_code=response.get("status", 0),
                content=body,
                headers=response_headers,
                encoding="utf-8" if _is_textual(mime_type) else None,
                request_body_signature=_request_body_signature(request),
            )
        )
    write_fixtures(out_path, exchanges)
    return len(exchanges)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--har", required=True, type=Path, help="Path to the .har file")
    parser.add_argument(
        "--url-contains",
        required=True,
        action="append",
        help="Substring to match against request URLs; repeatable",
    )
    parser.add_argument(
        "--out", required=True, type=Path, help="Output JSONL fixture path"
    )
    args = parser.parse_args()

    written = extract_fixtures_from_har(args.har, args.url_contains, args.out)
    print(f"Wrote {written} exchange(s) to {args.out}")


if __name__ == "__main__":  # pragma: no cover
    main()
