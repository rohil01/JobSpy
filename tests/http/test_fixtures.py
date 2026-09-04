import base64

import pytest

from jobsniffer.http.exceptions import FixtureFileError
from jobsniffer.http.fixtures import (
    RecordedExchange,
    append_fixture,
    compute_body_signature,
    load_fixtures,
    write_fixtures,
)


def test_recorded_exchange_round_trips_through_json_line():
    exchange = RecordedExchange.from_bytes(
        method="get",
        url="https://example.com/jobs?q=a",
        status_code=200,
        content=b'{"jobs": []}',
        headers={"content-type": "application/json"},
    )
    restored = RecordedExchange.from_json_line(exchange.to_json_line())
    assert restored == exchange
    assert restored.method == "GET"
    assert restored.content == b'{"jobs": []}'


def test_content_property_decodes_base64():
    exchange = RecordedExchange(
        method="GET",
        url="https://example.com",
        status_code=200,
        content_b64=base64.b64encode(b"hello").decode("ascii"),
    )
    assert exchange.content == b"hello"


def test_load_fixtures_missing_file_raises(tmp_path):
    missing = tmp_path / "does-not-exist.jsonl"
    with pytest.raises(FixtureFileError, match="not found"):
        load_fixtures(missing)


def test_load_fixtures_malformed_line_raises(tmp_path):
    path = tmp_path / "fixtures.jsonl"
    path.write_text("not json at all\n")
    with pytest.raises(FixtureFileError, match="Malformed fixture"):
        load_fixtures(path)


def test_load_fixtures_skips_blank_lines(tmp_path):
    path = tmp_path / "fixtures.jsonl"
    exchange = RecordedExchange.from_bytes(
        method="GET", url="https://example.com", status_code=200, content=b"x"
    )
    path.write_text(f"\n{exchange.to_json_line()}\n\n")
    loaded = load_fixtures(path)
    assert loaded == [exchange]


def test_append_fixture_creates_parent_dir_and_appends(tmp_path):
    path = tmp_path / "nested" / "fixtures.jsonl"
    first = RecordedExchange.from_bytes(
        method="GET", url="https://example.com/1", status_code=200, content=b"a"
    )
    second = RecordedExchange.from_bytes(
        method="GET", url="https://example.com/2", status_code=200, content=b"b"
    )
    append_fixture(path, first)
    append_fixture(path, second)
    assert load_fixtures(path) == [first, second]


def test_compute_body_signature_none_for_no_body():
    assert compute_body_signature() is None


def test_compute_body_signature_stable_for_equivalent_json_regardless_of_key_order():
    sig_a = compute_body_signature(json_body={"a": 1, "b": 2})
    sig_b = compute_body_signature(json_body={"b": 2, "a": 1})
    assert sig_a == sig_b


def test_compute_body_signature_differs_for_different_bodies():
    sig_a = compute_body_signature(json_body={"a": 1})
    sig_b = compute_body_signature(json_body={"a": 2})
    assert sig_a != sig_b


def test_compute_body_signature_handles_raw_bytes_and_str_and_dict_data():
    assert compute_body_signature(data=b"raw") is not None
    assert compute_body_signature(data="raw") is not None
    assert compute_body_signature(data={"k": "v"}) is not None
    # bytes and str forms of the same content are equivalent on the wire,
    # so they're expected to produce the same signature.
    assert compute_body_signature(data=b"raw") == compute_body_signature(data="raw")
    assert compute_body_signature(data=b"raw") != compute_body_signature(data=b"other")


def test_from_bytes_strips_set_cookie_header():
    exchange = RecordedExchange.from_bytes(
        method="GET",
        url="https://example.com",
        status_code=200,
        content=b"x",
        headers={
            "content-type": "text/html",
            "Set-Cookie": "JSESSIONID=abc123; Secure",
        },
    )
    assert "Set-Cookie" not in exchange.headers
    assert "set-cookie" not in exchange.headers
    assert exchange.headers == {"content-type": "text/html"}


def test_from_bytes_is_an_allow_list_not_a_deny_list():
    """Headers have zero functional value for replay/parsing (nothing
    reads them back), so only a small allow-list of genuinely useful
    names survives -- everything else is dropped, not just known-bad
    names. This is what actually protects against the next site leaking
    credentials under a header name nobody thought to deny-list."""
    exchange = RecordedExchange.from_bytes(
        method="GET",
        url="https://example.com",
        status_code=200,
        content=b"x",
        headers={
            "AUTHORIZATION": "Bearer secret-token",
            "Cookie": "session=abc",
            "Proxy-Authorization": "Basic xyz",
            "X-Amz-Security-Token": "also-secret",
            "x-some-other-header": "not-on-the-allow-list-either",
            "Content-Type": "application/json",
            "content-length": "123",
        },
    )
    assert exchange.headers == {
        "Content-Type": "application/json",
        "content-length": "123",
    }


def test_write_fixtures_upserts_by_method_url_and_body_signature(tmp_path):
    """Re-running a fixture-generation tool against the same source must
    not silently grow the file with a stale duplicate that ReplayClient's
    first-match-wins replay would then shadow behind the newer one."""
    path = tmp_path / "fixtures.jsonl"
    original = RecordedExchange.from_bytes(
        method="GET", url="https://example.com/jobs", status_code=200, content=b"old"
    )
    updated = RecordedExchange.from_bytes(
        method="GET", url="https://example.com/jobs", status_code=200, content=b"new"
    )
    unrelated = RecordedExchange.from_bytes(
        method="GET", url="https://example.com/other", status_code=200, content=b"z"
    )

    write_fixtures(path, [original, unrelated])
    write_fixtures(path, [updated])

    loaded = load_fixtures(path)
    assert len(loaded) == 2
    matching = next(e for e in loaded if e.url == "https://example.com/jobs")
    assert matching.content == b"new"
