from pathlib import Path

from jobsniffer.http.fixtures import load_fixtures
from jobsniffer.http.replay_client import RecordedResponse
from jobsniffer.indeed.detail import fetch_job_detail
from tests.indeed._fakes import FakeResponse

FIXTURES = Path(__file__).parent.parent / "fixtures" / "indeed.jsonl"


class FakeSession:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._response


def test_fetch_job_detail_sends_expected_url_and_params():
    session = FakeSession(FakeResponse(ok=True, text='{"body": {}}'))
    fetch_job_detail(session, base_url="https://www.indeed.com", job_key="abc123")
    url, kwargs = session.calls[0]
    assert url == "https://www.indeed.com/viewjob"
    assert kwargs["params"] == {"jk": "abc123", "from": "vjs"}


def test_fetch_job_detail_returns_none_on_non_ok_response():
    session = FakeSession(FakeResponse(ok=False, text=""))
    assert fetch_job_detail(session, base_url="https://x", job_key="k") is None


def test_fetch_job_detail_returns_none_on_malformed_json():
    session = FakeSession(FakeResponse(ok=True, text="not json"))
    assert fetch_job_detail(session, base_url="https://x", job_key="k") is None


def test_fetch_job_detail_returns_none_when_body_is_not_a_dict():
    session = FakeSession(FakeResponse(ok=True, text='{"body": "unexpected string"}'))
    assert fetch_job_detail(session, base_url="https://x", job_key="k") is None


def test_fetch_job_detail_returns_the_real_recorded_known_job_body():
    """Verifies against the same known ground truth as test_parse.py:
    jk=20d6afbf6595234e -> title "Website Developer & Digital Marketing",
    salaryMin/Max 60000/65000. Uses RecordedResponse directly (rather than
    a hand re-wrapped FakeResponse) -- it already wraps a RecordedExchange
    into exactly the .ok/.text/.json() shape fetch_job_detail expects."""
    exchanges = load_fixtures(FIXTURES)
    detail_exchange = next(e for e in exchanges if "jk=20d6afbf6595234e" in e.url)
    session = FakeSession(RecordedResponse(detail_exchange))

    body = fetch_job_detail(
        session, base_url="https://www.indeed.com", job_key="20d6afbf6595234e"
    )

    assert body is not None
    assert body["jobTitle"] == "Website Developer & Digital Marketing"
    assert body["salaryInfoModel"]["salaryMin"] == 60000
    assert body["salaryInfoModel"]["salaryMax"] == 65000
