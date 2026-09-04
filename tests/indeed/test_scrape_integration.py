"""End-to-end test of Indeed.scrape() wired against real recorded data.

Uses a FakeSession (matching the HttpClient protocol) that serves the real
search-page HTML and /viewjob JSON from tests/fixtures/indeed.jsonl, keyed
by request shape rather than exact incidental URL (query params like
`from=searchOnHP` were artifacts of one particular real browsing session,
not something Indeed.scrape() is expected to reproduce byte-for-byte) --
see tests/indeed/test_search.py and test_detail.py for why ReplayClient's
exact-URL matching isn't the right tool for this test.
"""

import json
from pathlib import Path

import pytest

from jobsniffer.exception import IndeedException
from jobsniffer.http.fixtures import load_fixtures
from jobsniffer.indeed import Indeed
from jobsniffer.model import Country, DescriptionFormat, ScraperInput, Site
from tests.indeed._fakes import FakeResponse

FIXTURES = Path(__file__).parent.parent / "fixtures" / "indeed.jsonl"


class FakeIndeedSession:
    """Routes by path/param shape to the matching real fixture content:
    /jobs -> the recorded search page; /viewjob?jk=... -> the matching
    recorded detail response (or a 404-shaped response for any other
    jobkey, since only 2 of the page's 40 jobs were captured in detail)."""

    def __init__(self, exchanges):
        self._exchanges = exchanges
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/jobs"):
            search = next(e for e in self._exchanges if e.url.rstrip("?").split("?")[0].endswith("/jobs"))
            return FakeResponse(ok=True, text=search.content.decode("utf-8"))
        if url.endswith("/viewjob"):
            job_key = kwargs["params"]["jk"]
            for exchange in self._exchanges:
                if f"jk={job_key}" in exchange.url:
                    return FakeResponse(ok=True, text=exchange.content.decode("utf-8"))
            return FakeResponse(ok=False, text="")
        raise AssertionError(f"unexpected URL in test: {url}")

    def post(self, url, **kwargs):  # pragma: no cover -- graphql fallback not exercised here
        raise AssertionError("HTML path should not fall back to graphql in this test")


def _scraper_input(**overrides):
    defaults = {
        "site_type": [Site.INDEED],
        "search_term": "marketing",
        "location": "Framingham, MA",
        "country": Country.USA,
        "results_wanted": 5,
        "description_format": DescriptionFormat.HTML,
    }
    defaults.update(overrides)
    return ScraperInput(**defaults)


def test_scrape_returns_the_known_job_with_full_detail_and_salary():
    exchanges = load_fixtures(FIXTURES)
    indeed = Indeed()
    indeed.session = FakeIndeedSession(exchanges)

    response = indeed.scrape(_scraper_input(results_wanted=40))

    known = next(job for job in response.jobs if job.id == "in-20d6afbf6595234e")
    assert known.title == "Website Developer & Digital Marketing"
    assert len(known.description) == 7259
    assert known.compensation.min_amount == 60000
    assert known.compensation.max_amount == 65000
    assert known.compensation.currency == "USD"
    assert known.job_url == "https://www.indeed.com/viewjob?jk=20d6afbf6595234e"


def test_scrape_falls_back_to_snippet_when_detail_fetch_unavailable():
    """Only 2 of the page's 40 jobs have a recorded detail fixture -- the
    other 38 must degrade to the search-result snippet, not an empty
    description (P8)."""
    exchanges = load_fixtures(FIXTURES)
    indeed = Indeed()
    indeed.session = FakeIndeedSession(exchanges)

    response = indeed.scrape(_scraper_input(results_wanted=40))

    undetailed = [j for j in response.jobs if j.id != "in-20d6afbf6595234e" and j.id != "in-f96cadb65ecc3d20"]
    assert undetailed, "expected at least one job with no recorded detail fixture"
    assert all(job.description for job in undetailed)


def test_scrape_respects_results_wanted_and_offset():
    exchanges = load_fixtures(FIXTURES)
    indeed = Indeed()
    indeed.session = FakeIndeedSession(exchanges)

    response = indeed.scrape(_scraper_input(results_wanted=3, offset=0))
    assert len(response.jobs) == 3


def test_scrape_with_offset_does_not_detail_fetch_the_skipped_prefix():
    """N jobs before `offset` are collected during search (needed to know
    their jobkeys/ordering) but must NOT trigger a /viewjob detail fetch --
    that would be pure waste for records the caller is discarding anyway."""
    exchanges = load_fixtures(FIXTURES)
    indeed = Indeed()
    session = FakeIndeedSession(exchanges)
    indeed.session = session

    response = indeed.scrape(_scraper_input(results_wanted=2, offset=3))

    assert len(response.jobs) == 2
    detail_fetch_count = sum(1 for url, _ in session.calls if url.endswith("/viewjob"))
    assert detail_fetch_count == 2


def test_scrape_falls_back_to_graphql_when_html_search_is_blocked():
    class BlockedSearchSession:
        def __init__(self):
            self.graphql_called = False

        def get(self, url, **kwargs):
            return FakeResponse(ok=True, text="<html>Please verify you're human</html>")

        def post(self, url, **kwargs):
            self.graphql_called = True
            return FakeResponse(ok=True, text='{"data": {"jobSearch": {"pageInfo": {"nextCursor": null}, "results": []}}}')

    session = BlockedSearchSession()
    indeed = Indeed()
    indeed.session = session

    response = indeed.scrape(_scraper_input(results_wanted=5))

    assert session.graphql_called is True
    assert response.jobs == []


def test_scrape_does_not_fall_back_to_graphql_on_a_legitimately_empty_search():
    """A search page that renders normally (mosaic-provider-jobcards
    present) but has zero results -- a real, narrow search term matching
    nothing -- must NOT trigger the GraphQL fallback. Falling back here
    would waste a request against a fragile shared-credential path for a
    perfectly ordinary "no matches" outcome."""

    class LegitimatelyEmptySession:
        def __init__(self):
            self.graphql_called = False

        def get(self, url, **kwargs):
            return FakeResponse(
                ok=True,
                text=(
                    'window.mosaic.providerData["mosaic-provider-jobcards"]='
                    '{"metaData": {"mosaicProviderJobCardsModel": {"results": []}}};'
                ),
            )

        def post(self, url, **kwargs):
            self.graphql_called = True
            raise AssertionError("should not fall back to graphql for a legitimate empty result")

    session = LegitimatelyEmptySession()
    indeed = Indeed()
    indeed.session = session

    response = indeed.scrape(_scraper_input(results_wanted=5))

    assert session.graphql_called is False
    assert response.jobs == []


def test_scrape_raises_when_country_is_none():
    indeed = Indeed()
    with pytest.raises(IndeedException, match="country"):
        indeed.scrape(_scraper_input(country=None))


class _RepeatingSearchSession:
    """Always returns the same single job on /jobs, and a matching detail
    on /viewjob -- used to exercise the "page returned only already-seen
    jobs, stop paginating" branch directly."""

    _SEARCH_HTML = (
        'window.mosaic.providerData["mosaic-provider-jobcards"]='
        '{"metaData": {"mosaicProviderJobCardsModel": {"results": '
        '[{"jobkey": "dup1", "title": "Repeated Job", "company": "Acme", '
        '"snippet": "short snippet"}]}}};'
    )

    def get(self, url, **kwargs):
        if url.endswith("/jobs"):
            return FakeResponse(ok=True, text=self._SEARCH_HTML)
        return FakeResponse(ok=False, text="")


def test_collect_html_search_results_stops_when_a_page_returns_only_already_seen_jobs():
    indeed = Indeed()
    indeed.session = _RepeatingSearchSession()
    raw_jobs, blocked = indeed._collect_html_search_results(
        _scraper_input(results_wanted=10), "https://www.indeed.com"
    )
    assert blocked is False
    assert len(raw_jobs) == 1
    assert raw_jobs[0]["jobkey"] == "dup1"


def test_build_job_post_converts_markdown_on_html_path():
    indeed = Indeed()

    class DetailSession:
        def get(self, url, **kwargs):
            return FakeResponse(
                ok=True,
                text=(
                    '{"body": {"jobInfoWrapperModel": {"jobInfoModel": '
                    '{"sanitizedJobDescription": "<p>Great <b>job</b></p>"}}, '
                    '"salaryInfoModel": null}}'
                ),
            )

    indeed.session = DetailSession()
    job = {"jobkey": "md1", "title": "Writer", "company": "Acme"}
    post = indeed._build_job_post(
        job, _scraper_input(description_format=DescriptionFormat.MARKDOWN), "https://www.indeed.com"
    )
    assert "<p>" not in post.description
    assert "job" in post.description.lower()


def _make_graphql_job(key, title=None):
    return {
        "key": key,
        "title": title or key,
        "datePublished": 1784178000000,
        "description": {"html": "<p>desc</p>"},
        "location": {
            "city": "Austin",
            "admin1Code": "TX",
            "countryCode": "US",
            "formatted": {"short": "Austin, TX", "long": "Austin, TX, United States"},
        },
        "compensation": {"estimated": None, "baseSalary": None, "currencyCode": "USD"},
        "attributes": [],
        "employer": None,
        "recruit": None,
    }


def test_scrape_graphql_paginates_and_dedupes_across_pages():
    indeed = Indeed()

    pages = [
        {
            "data": {
                "jobSearch": {
                    "pageInfo": {"nextCursor": "page2"},
                    "results": [{"job": _make_graphql_job("g1")}, {"job": _make_graphql_job("g2")}],
                }
            }
        },
        {
            "data": {
                "jobSearch": {
                    "pageInfo": {"nextCursor": None},
                    # g2 repeated across pages -- must be deduped, not double-counted
                    "results": [{"job": _make_graphql_job("g2")}, {"job": _make_graphql_job("g3")}],
                }
            }
        },
    ]

    class PaginatedGraphqlSession:
        def __init__(self):
            self.call_count = 0

        def post(self, url, **kwargs):
            page = pages[self.call_count]
            self.call_count += 1
            return FakeResponse(ok=True, text=json.dumps(page))

    indeed.session = PaginatedGraphqlSession()
    job_list = indeed._scrape_graphql(
        _scraper_input(results_wanted=10), "https://www.indeed.com", "US"
    )

    assert {job.id for job in job_list} == {"in-g1", "in-g2", "in-g3"}
    assert indeed.session.call_count == 2


def test_scrape_graphql_stops_mid_page_once_target_reached():
    indeed = Indeed()

    body = {
        "data": {
            "jobSearch": {
                "pageInfo": {"nextCursor": "more-available-but-unneeded"},
                "results": [{"job": _make_graphql_job("only1")}, {"job": _make_graphql_job("only2")}],
            }
        }
    }

    class SingleResponseSession:
        def post(self, url, **kwargs):
            return FakeResponse(ok=True, text=json.dumps(body))

    indeed.session = SingleResponseSession()
    job_list = indeed._scrape_graphql(
        _scraper_input(results_wanted=1, offset=0), "https://www.indeed.com", "US"
    )

    assert len(job_list) == 1
    assert job_list[0].id == "in-only1"
