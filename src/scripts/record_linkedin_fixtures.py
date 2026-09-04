"""src.scripts.record_linkedin_fixtures

Seeds tests/fixtures/linkedin.jsonl with real responses from LinkedIn's
anonymous jobs-guest API, via jobsniffer.http.ReplayClient's record mode.

No HAR ground truth exists for this path (the only LinkedIn capture in this
project's input/ is an authenticated Voyager session, a different API --
see docs/2026-07-27-jobsniffer-modernization-plan.md, "Ground truth from
the HAR captures"). This script performs exactly the two requests
jobsniffer.linkedin.LinkedIn.scrape()/_get_job_details() make -- one search
page, one job detail page for the first result -- so the recorded
responses are real fixtures for testing the #374 empty-description fix and
the search-card parser, not synthetic data.

Anonymous/guest endpoints only: no cookies, no login, no li_at session
token, matching the modernization plan's explicit choice to avoid
LinkedIn account risk. Run interactively, once, when fixtures need
(re)seeding -- not part of the normal test suite.

Usage:
    uv run python -m scripts.record_linkedin_fixtures
"""

from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup

from jobsniffer.http import ReplayClient
from jobsniffer.linkedin.constant import headers
from jobsniffer.linkedin.util import job_id_from_search_card

BASE_URL = "https://www.linkedin.com"
FIXTURE_PATH = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "linkedin.jsonl"


def _first_job_id(search_html: str) -> str | None:
    """Uses jobsniffer.linkedin.util.job_id_from_search_card -- the same
    function LinkedIn.scrape() calls -- so this can't silently drift from
    the real scraper's parsing and record a fixture for the wrong job."""
    soup = BeautifulSoup(search_html, "html.parser")
    for job_card in soup.find_all("div", class_="base-search-card"):
        job_id = job_id_from_search_card(job_card)
        if job_id is not None:
            return job_id
    return None


def main() -> None:
    client = ReplayClient(FIXTURE_PATH, mode="record")
    client.headers.update(headers)

    print("Recording LinkedIn guest search...")
    search_response = client.get(
        f"{BASE_URL}/jobs-guest/jobs/api/seeMoreJobPostings/search?",
        params={
            "keywords": "software engineer",
            "location": "United States",
            "pageNum": 0,
            "start": 0,
        },
        timeout=10,
    )
    print(f"  status={search_response.status_code} bytes={len(search_response.content)}")

    job_id = _first_job_id(search_response.text)
    if job_id is None:
        print("No job cards found in search response -- detail page not recorded.")
        client.close()
        return

    print(f"Recording LinkedIn job detail for job_id={job_id}...")
    detail_response = client.get(f"{BASE_URL}/jobs/view/{job_id}", timeout=5)
    print(f"  status={detail_response.status_code} bytes={len(detail_response.content)}")

    client.close()
    print(f"Fixtures written to {FIXTURE_PATH}")


if __name__ == "__main__":  # pragma: no cover
    main()
