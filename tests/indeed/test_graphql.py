import json

from jobsniffer.indeed.graphql import (
    build_filters,
    fetch_page,
    job_post_from_graphql_result,
)
from jobsniffer.model import DescriptionFormat, JobType
from tests.indeed._fakes import FakeResponse


def test_build_filters_hours_old_takes_priority():
    filters = build_filters(hours_old=48, easy_apply=True, job_type=JobType.FULL_TIME, is_remote=True)
    assert "dateOnIndeed" in filters
    assert "48h" in filters


def test_build_filters_easy_apply_when_no_hours_old():
    filters = build_filters(hours_old=None, easy_apply=True, job_type=None, is_remote=False)
    assert "indeedApplyScope" in filters


def test_build_filters_job_type_and_remote_composite():
    filters = build_filters(hours_old=None, easy_apply=None, job_type=JobType.FULL_TIME, is_remote=True)
    assert "CF3CP" in filters
    assert "DSQF7" in filters


def test_build_filters_empty_when_nothing_specified():
    assert build_filters(hours_old=None, easy_apply=None, job_type=None, is_remote=False) == ""


class FakeSession:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._response


def test_fetch_page_returns_empty_on_non_ok_response():
    session = FakeSession(FakeResponse(ok=False, text=""))
    results, cursor = fetch_page(
        session,
        api_url="https://apis.indeed.com/graphql",
        api_country_code="US",
        search_term="engineer",
        location="Austin, TX",
        distance=25,
        hours_old=None,
        easy_apply=None,
        job_type=None,
        is_remote=False,
        cursor=None,
    )
    assert results == []
    assert cursor is None


def test_fetch_page_returns_empty_when_job_search_missing():
    session = FakeSession(FakeResponse(ok=True, text=json.dumps({"data": {}})))
    results, cursor = fetch_page(
        session,
        api_url="https://apis.indeed.com/graphql",
        api_country_code="US",
        search_term="x",
        location=None,
        distance=None,
        hours_old=None,
        easy_apply=None,
        job_type=None,
        is_remote=False,
        cursor=None,
    )
    assert results == []
    assert cursor is None


def test_fetch_page_extracts_results_and_next_cursor():
    body = {
        "data": {
            "jobSearch": {
                "pageInfo": {"nextCursor": "abc123"},
                "results": [{"job": {"key": "k1", "title": "Engineer"}}],
            }
        }
    }
    session = FakeSession(FakeResponse(ok=True, text=json.dumps(body)))
    results, cursor = fetch_page(
        session,
        api_url="https://apis.indeed.com/graphql",
        api_country_code="US",
        search_term="engineer",
        location="Austin, TX",
        distance=25,
        hours_old=None,
        easy_apply=None,
        job_type=None,
        is_remote=False,
        cursor=None,
    )
    assert results == [{"job": {"key": "k1", "title": "Engineer"}}]
    assert cursor == "abc123"
    _url, kwargs = session.calls[0]
    assert kwargs["headers"]["indeed-co"] == "US"
    assert "engineer" in kwargs["json"]["query"]


def test_fetch_page_defaults_distance_to_50_when_none():
    """Live-caught bug: distance=None (ScraperInput's actual default)
    used to render as the literal string "radius: None" in the GraphQL
    query text -- invalid, and silently rejected by the API as an
    errored/empty jobSearch rather than raising. Confirmed live against
    the real endpoint before fixing: identical query with radius: 50
    returns 100 results with no errors; radius: None returns zero."""
    session = FakeSession(
        FakeResponse(
            ok=True,
            text=json.dumps({"data": {"jobSearch": {"pageInfo": {}, "results": []}}}),
        )
    )
    fetch_page(
        session,
        api_url="https://apis.indeed.com/graphql",
        api_country_code="US",
        search_term="engineer",
        location="Austin, TX",
        distance=None,
        hours_old=None,
        easy_apply=None,
        job_type=None,
        is_remote=False,
        cursor=None,
    )
    _url, kwargs = session.calls[0]
    assert "radius: 50" in kwargs["json"]["query"]
    assert "radius: None" not in kwargs["json"]["query"]


def test_fetch_page_uses_explicit_distance_when_provided():
    session = FakeSession(
        FakeResponse(
            ok=True,
            text=json.dumps({"data": {"jobSearch": {"pageInfo": {}, "results": []}}}),
        )
    )
    fetch_page(
        session,
        api_url="https://apis.indeed.com/graphql",
        api_country_code="US",
        search_term="engineer",
        location="Austin, TX",
        distance=10,
        hours_old=None,
        easy_apply=None,
        job_type=None,
        is_remote=False,
        cursor=None,
    )
    _url, kwargs = session.calls[0]
    assert "radius: 10" in kwargs["json"]["query"]


_SAMPLE_JOB = {
    "key": "abc123def456",
    "title": "Senior Software Engineer",
    "datePublished": 1784178000000,
    "description": {"html": "<p>Great <b>job</b></p>"},
    "location": {
        "city": "Austin",
        "admin1Code": "TX",
        "countryCode": "US",
        "formatted": {"short": "Austin, TX", "long": "Austin, TX, United States"},
    },
    "compensation": {
        "estimated": None,
        "baseSalary": {"unitOfWork": "YEAR", "range": {"min": 100000, "max": 150000}},
        "currencyCode": "USD",
    },
    "attributes": [{"key": "FULLTIME", "label": "Full-time"}],
    "employer": {
        "relativeCompanyPageUrl": "/cmp/Acme-Corp",
        "name": "Acme Corp",
        "dossier": {
            "employerDetails": {
                "addresses": ["123 Main St"],
                "industry": "Iv1_TECH_SOFTWARE",
                "employeesLocalizedLabel": "501-1000",
                "revenueLocalizedLabel": "$100M-$500M",
                "briefDescription": "We make things.",
            },
            "images": {"squareLogoUrl": "https://example.com/logo.png"},
            "links": {"corporateWebsite": "https://acme.example.com"},
        },
    },
    "recruit": {"viewJobUrl": "https://acme.example.com/apply/abc123"},
}


def test_job_post_from_graphql_result_maps_known_fields():
    post = job_post_from_graphql_result(
        _SAMPLE_JOB, base_url="https://www.indeed.com", description_format=DescriptionFormat.HTML
    )
    assert post.id == "in-abc123def456"
    assert post.title == "Senior Software Engineer"
    assert post.company_name == "Acme Corp"
    assert post.job_url == "https://www.indeed.com/viewjob?jk=abc123def456"
    assert post.job_url_direct == "https://acme.example.com/apply/abc123"
    assert post.location.city == "Austin"
    assert post.location.state == "TX"
    assert post.compensation.min_amount == 100000
    assert post.compensation.max_amount == 150000
    assert post.company_logo == "https://example.com/logo.png"
    assert post.company_industry == "Tech Software"
    assert JobType.FULL_TIME in post.job_type
    assert post.is_remote is False


def test_job_post_from_graphql_result_detects_remote_from_location():
    job = dict(_SAMPLE_JOB)
    job["location"] = dict(job["location"])
    job["location"]["formatted"] = {"short": "Remote", "long": "Remote, United States"}
    post = job_post_from_graphql_result(
        job, base_url="https://www.indeed.com", description_format=DescriptionFormat.HTML
    )
    assert post.is_remote is True


def test_job_post_from_graphql_result_converts_markdown_when_requested():
    post = job_post_from_graphql_result(
        _SAMPLE_JOB,
        base_url="https://www.indeed.com",
        description_format=DescriptionFormat.MARKDOWN,
    )
    assert "**job**" in post.description or "job" in post.description
    assert "<p>" not in post.description


def test_job_post_from_graphql_result_handles_missing_employer():
    job = dict(_SAMPLE_JOB)
    job["employer"] = None
    post = job_post_from_graphql_result(
        job, base_url="https://www.indeed.com", description_format=DescriptionFormat.HTML
    )
    assert post.company_name is None
    assert post.company_url is None
    assert post.company_url_direct is None
