"""jobsniffer.indeed.detail

Step 2 of Indeed's two-step flow: GET /viewjob?jk={jobkey} and extract the
full description + authoritative salary. See jobsniffer.indeed.search for
step 1 and jobsniffer.indeed.parse's module docstring for why both steps
are required.
"""

from __future__ import annotations

import json

from jobsniffer.http.protocol import HttpClient


def fetch_job_detail(
    session: HttpClient, *, base_url: str, job_key: str, timeout: float = 10.0
) -> dict | None:
    """Fetches /viewjob for a single job and returns its parsed `body`
    dict, or None if the request failed or the response wasn't the
    expected shape (P8 -- callers get an explicit "no detail available"
    rather than a fabricated empty description)."""
    response = session.get(
        f"{base_url}/viewjob",
        params={"jk": job_key, "from": "vjs"},
        timeout=timeout,
    )
    if not response.ok:
        return None
    try:
        data = response.json()
    except json.JSONDecodeError:
        return None
    body = data.get("body")
    return body if isinstance(body, dict) else None
