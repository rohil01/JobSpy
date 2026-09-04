import pytest

from jobsniffer.http.curl_client import CurlCffiClient
from jobsniffer.http.exceptions import FixtureNotFoundError
from jobsniffer.http.fixtures import RecordedExchange, append_fixture
from jobsniffer.http.replay_client import RecordedResponse, ReplayClient


@pytest.fixture
def fixture_path(tmp_path):
    path = tmp_path / "site.jsonl"
    append_fixture(
        path,
        RecordedExchange.from_bytes(
            method="GET",
            url="https://example.com/jobs?q=engineer",
            status_code=200,
            content=b'{"jobs": ["a"]}',
            headers={"content-type": "application/json"},
        ),
    )
    append_fixture(
        path,
        RecordedExchange.from_bytes(
            method="POST",
            url="https://example.com/graphql",
            status_code=200,
            content=b'{"data": {"n": 1}}',
            request_body_signature="sig-1",
        ),
    )
    append_fixture(
        path,
        RecordedExchange.from_bytes(
            method="POST",
            url="https://example.com/graphql",
            status_code=200,
            content=b'{"data": {"n": 2}}',
            request_body_signature="sig-2",
        ),
    )
    return path


def test_replay_matches_exact_url(fixture_path):
    client = ReplayClient(fixture_path)
    response = client.get("https://example.com/jobs?q=engineer")
    assert response.status_code == 200
    assert response.ok is True
    assert response.json() == {"jobs": ["a"]}


def test_replay_merges_params_into_url_before_matching(fixture_path):
    client = ReplayClient(fixture_path)
    response = client.get("https://example.com/jobs", params={"q": "engineer"})
    assert response.json() == {"jobs": ["a"]}


def test_replay_disambiguates_same_url_by_body_signature(fixture_path, monkeypatch):
    import jobsniffer.http.replay_client as replay_module

    monkeypatch.setattr(
        replay_module, "compute_body_signature", lambda **_: "sig-2"
    )
    client = ReplayClient(fixture_path)
    response = client.post("https://example.com/graphql", json={"query": "whatever"})
    assert response.json() == {"data": {"n": 2}}


def test_replay_missing_fixture_raises_with_method_and_url(fixture_path):
    client = ReplayClient(fixture_path)
    with pytest.raises(FixtureNotFoundError) as exc_info:
        client.get("https://example.com/nonexistent")
    assert exc_info.value.method == "GET"
    assert exc_info.value.url == "https://example.com/nonexistent"


def test_record_mode_appends_and_serves_from_memory(tmp_path):
    class FakeResponse:
        def __init__(self):
            self.status_code = 200
            self.headers = {"content-type": "application/json"}
            self.content = b'{"ok": true}'

    class FakeRecorder:
        def __init__(self):
            self.calls = []
            self.closed = False

        def request(self, method, url, **kwargs):
            self.calls.append((method, url))
            return FakeResponse()

        def close(self):
            self.closed = True

    path = tmp_path / "recorded.jsonl"
    fake_recorder = FakeRecorder()
    client = ReplayClient(path, mode="record", recorder=fake_recorder)

    response = client.get("https://example.com/live")
    assert response.json() == {"ok": True}
    assert fake_recorder.calls == [("GET", "https://example.com/live")]

    # A record-mode client never reads its own recordings back -- it always
    # hits the live recorder (verified above). Persisted-to-disk fixtures are
    # served by a *separate* ReplayClient in replay mode, loaded from the
    # fixture file this session wrote.
    assert client._exchanges == []
    replay_client = ReplayClient(path)
    replayed = replay_client.get("https://example.com/live")
    assert replayed.json() == {"ok": True}

    client.close()
    assert fake_recorder.closed is True


def test_record_mode_without_explicit_recorder_creates_a_real_curl_client(tmp_path):
    path = tmp_path / "recorded.jsonl"
    client = ReplayClient(path, mode="record")
    assert isinstance(client._recorder, CurlCffiClient)


def test_recorded_response_text_is_lazy_and_never_crashes_on_binary_content():
    """ZipRecruiter's protobuf detail responses are never valid UTF-8 --
    constructing a RecordedResponse for one must not raise before the
    caller reads `.content` instead of `.text`, matching real
    curl_cffi.requests.Response behavior (its .text is also lazy)."""
    binary_exchange = RecordedExchange.from_bytes(
        method="POST",
        url="https://www.ziprecruiter.com/GetJobDetails",
        status_code=200,
        content=b"\xff\xfe\x00binary-protobuf-garbage\x80\x81",
        encoding=None,
    )
    response = RecordedResponse(binary_exchange)  # must not raise
    assert response.content == b"\xff\xfe\x00binary-protobuf-garbage\x80\x81"
    # .text is replacement-decoded, not raised, when actually accessed
    assert isinstance(response.text, str)


def test_recorded_response_text_falls_back_on_unknown_encoding_name():
    exchange = RecordedExchange.from_bytes(
        method="GET",
        url="https://example.com/weird-encoding",
        status_code=200,
        content=b"hello",
        encoding="not-a-real-codec",
    )
    response = RecordedResponse(exchange)
    assert response.text == "hello"
