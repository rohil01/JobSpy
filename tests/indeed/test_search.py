from pathlib import Path

from jobsniffer.http.fixtures import load_fixtures
from jobsniffer.indeed.search import build_search_params, fetch_search_page
from tests.indeed._fakes import FakeResponse

FIXTURES = Path(__file__).parent.parent / "fixtures" / "indeed.jsonl"


class FakeSession:
    """Matches the HttpClient protocol's shape without depending on
    ReplayClient's exact-URL matching -- fetch_search_page's URL/params
    construction is verified directly via recorded calls, while its
    response handling is exercised against the real recorded search page
    body, decoupling "did we build the right request" from "did we parse
    the response correctly" (the latter already has its own exhaustive
    tests in tests/indeed/test_parse.py)."""

    def __init__(self, response: FakeResponse):
        self._response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._response


def test_build_search_params_includes_required_fields():
    params = build_search_params(
        search_term="software engineer",
        location="Austin, TX",
        distance=25,
        hours_old=None,
    )
    assert params == {"q": "software engineer", "l": "Austin, TX", "start": 0, "radius": 25}


def test_build_search_params_converts_hours_old_to_whole_days():
    params = build_search_params(
        search_term="x", location=None, distance=None, hours_old=72
    )
    assert params["fromage"] == 3


def test_build_search_params_minimum_one_day_for_small_hours_old():
    params = build_search_params(
        search_term="x", location=None, distance=None, hours_old=5
    )
    assert params["fromage"] == 1


def test_build_search_params_omits_none_values():
    params = build_search_params(
        search_term="x", location=None, distance=None, hours_old=None
    )
    assert "l" not in params
    assert "radius" not in params
    assert "fromage" not in params


def test_fetch_search_page_sends_expected_url_and_params():
    session = FakeSession(FakeResponse(ok=True, text='window.mosaic.providerData["mosaic-provider-jobcards"]={"metaData": {}};'))
    fetch_search_page(
        session,
        base_url="https://www.indeed.com",
        search_term="marketing",
        location="Boston, MA",
        distance=None,
        hours_old=None,
        start=10,
    )
    url, kwargs = session.calls[0]
    assert url == "https://www.indeed.com/jobs"
    assert kwargs["params"] == {"q": "marketing", "l": "Boston, MA", "start": 10}


def test_fetch_search_page_blocked_on_non_ok_response():
    session = FakeSession(FakeResponse(ok=False, text=""))
    page = fetch_search_page(
        session,
        base_url="https://www.indeed.com",
        search_term="x",
        location=None,
        distance=None,
        hours_old=None,
    )
    assert page.results == []
    assert page.blocked is True


def test_fetch_search_page_blocked_when_provider_marker_missing():
    """Distinguishes a block/CAPTCHA/markup-change page (no
    mosaic-provider-jobcards marker at all) from a page that rendered
    normally with zero results -- the two must not be conflated, since
    only the former should trigger Indeed.scrape()'s GraphQL fallback."""
    session = FakeSession(FakeResponse(ok=True, text="<html>Please verify you're human</html>"))
    page = fetch_search_page(
        session,
        base_url="https://www.indeed.com",
        search_term="x",
        location=None,
        distance=None,
        hours_old=None,
    )
    assert page.results == []
    assert page.blocked is True


def test_fetch_search_page_not_blocked_when_provider_present_but_empty():
    session = FakeSession(
        FakeResponse(
            ok=True,
            text=(
                'window.mosaic.providerData["mosaic-provider-jobcards"]='
                '{"metaData": {"mosaicProviderJobCardsModel": {"results": []}}};'
            ),
        )
    )
    page = fetch_search_page(
        session,
        base_url="https://www.indeed.com",
        search_term="an extremely narrow search term",
        location=None,
        distance=None,
        hours_old=None,
    )
    assert page.results == []
    assert page.blocked is False


def test_fetch_search_page_parses_the_real_recorded_search_page():
    """Response handling against real, known ground truth: this must
    yield the same 40 results (and the known job key) that
    tests/indeed/test_parse.py verifies directly against parse_search_results."""
    exchanges = load_fixtures(FIXTURES)
    search_html = exchanges[0].content.decode("utf-8")
    session = FakeSession(FakeResponse(ok=True, text=search_html))

    page = fetch_search_page(
        session,
        base_url="https://www.indeed.com",
        search_term="marketing",
        location="Framingham, MA",
        distance=None,
        hours_old=None,
    )

    assert page.blocked is False
    assert len(page.results) == 40
    assert "20d6afbf6595234e" in {job["jobkey"] for job in page.results}
