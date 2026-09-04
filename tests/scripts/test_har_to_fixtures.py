import base64
import json

from jobsniffer.http.fixtures import load_fixtures
from scripts.har_to_fixtures import extract_fixtures_from_har


def _har_entry(
    *,
    method="GET",
    url,
    status=200,
    mime_type="application/json",
    body_text=None,
    encoding="base64",
    response_headers=None,
    post_data=None,
):
    content = {"mimeType": mime_type}
    if body_text is not None:
        content["text"] = body_text
        if encoding:
            content["encoding"] = encoding
    request = {"method": method, "url": url, "headers": []}
    if post_data is not None:
        request["postData"] = post_data
    return {
        "request": request,
        "response": {
            "status": status,
            "headers": [
                {"name": k, "value": v} for k, v in (response_headers or {}).items()
            ],
            "content": content,
        },
    }


def _write_har(path, entries):
    path.write_text(json.dumps({"log": {"entries": entries}}))


def test_extracts_matching_base64_encoded_json_entry(tmp_path):
    har_path = tmp_path / "site.har"
    body = json.dumps({"jobs": [1, 2, 3]}).encode()
    _write_har(
        har_path,
        [
            _har_entry(
                url="https://example.com/jobs?q=a",
                body_text=base64.b64encode(body).decode("ascii"),
                mime_type="text/html",
                response_headers={"content-type": "text/html"},
            )
        ],
    )
    out_path = tmp_path / "fixtures.jsonl"

    written = extract_fixtures_from_har(har_path, ["example.com/jobs"], out_path)

    assert written == 1
    exchanges = load_fixtures(out_path)
    assert len(exchanges) == 1
    assert exchanges[0].content == body
    assert exchanges[0].url == "https://example.com/jobs?q=a"
    assert exchanges[0].encoding == "utf-8"  # text/html is textual


def test_skips_entries_that_do_not_match_any_url_substring(tmp_path):
    har_path = tmp_path / "site.har"
    _write_har(
        har_path,
        [
            _har_entry(
                url="https://example.com/unrelated",
                body_text=base64.b64encode(b"{}").decode("ascii"),
            )
        ],
    )
    out_path = tmp_path / "fixtures.jsonl"

    written = extract_fixtures_from_har(har_path, ["example.com/jobs"], out_path)

    assert written == 0
    assert not out_path.exists()


def test_skips_entries_with_no_captured_response_body(tmp_path):
    har_path = tmp_path / "site.har"
    _write_har(har_path, [_har_entry(url="https://example.com/jobs", body_text=None)])
    out_path = tmp_path / "fixtures.jsonl"

    written = extract_fixtures_from_har(har_path, ["example.com/jobs"], out_path)

    assert written == 0


def test_plain_text_body_without_base64_encoding_is_utf8_encoded_directly(tmp_path):
    har_path = tmp_path / "site.har"
    _write_har(
        har_path,
        [
            _har_entry(
                url="https://example.com/jobs",
                body_text='{"plain": true}',
                encoding=None,
            )
        ],
    )
    out_path = tmp_path / "fixtures.jsonl"

    extract_fixtures_from_har(har_path, ["example.com/jobs"], out_path)

    exchanges = load_fixtures(out_path)
    assert exchanges[0].content == b'{"plain": true}'


def test_binary_mime_type_gets_no_text_encoding(tmp_path):
    har_path = tmp_path / "site.har"
    raw = bytes([0xFF, 0xFE, 0x00, 0x01, 0x02])
    _write_har(
        har_path,
        [
            _har_entry(
                url="https://api.example.com/GetJobDetails",
                mime_type="application/proto",
                body_text=base64.b64encode(raw).decode("ascii"),
            )
        ],
    )
    out_path = tmp_path / "fixtures.jsonl"

    extract_fixtures_from_har(har_path, ["GetJobDetails"], out_path)

    exchanges = load_fixtures(out_path)
    assert exchanges[0].content == raw
    assert exchanges[0].encoding is None


def test_post_body_produces_a_request_body_signature(tmp_path):
    har_path = tmp_path / "site.har"
    _write_har(
        har_path,
        [
            _har_entry(
                method="POST",
                url="https://example.com/graphql",
                body_text=base64.b64encode(b'{"data": 1}').decode("ascii"),
                post_data={"mimeType": "application/json", "text": '{"query": "x"}'},
            )
        ],
    )
    out_path = tmp_path / "fixtures.jsonl"

    extract_fixtures_from_har(har_path, ["graphql"], out_path)

    exchanges = load_fixtures(out_path)
    assert exchanges[0].request_body_signature is not None


def test_get_request_has_no_body_signature(tmp_path):
    har_path = tmp_path / "site.har"
    _write_har(
        har_path,
        [
            _har_entry(
                url="https://example.com/jobs",
                body_text=base64.b64encode(b"{}").decode("ascii"),
            )
        ],
    )
    out_path = tmp_path / "fixtures.jsonl"

    extract_fixtures_from_har(har_path, ["example.com/jobs"], out_path)

    exchanges = load_fixtures(out_path)
    assert exchanges[0].request_body_signature is None


def test_multiple_url_contains_needles_are_ored_together(tmp_path):
    har_path = tmp_path / "site.har"
    body = base64.b64encode(b"{}").decode("ascii")
    _write_har(
        har_path,
        [
            _har_entry(url="https://example.com/search", body_text=body),
            _har_entry(url="https://example.com/detail", body_text=body),
            _har_entry(url="https://example.com/unrelated", body_text=body),
        ],
    )
    out_path = tmp_path / "fixtures.jsonl"

    written = extract_fixtures_from_har(
        har_path, ["/search", "/detail"], out_path
    )

    assert written == 2


def test_rerunning_against_the_same_har_upserts_rather_than_duplicates(tmp_path):
    """Re-running this tool against the same HAR (e.g. a re-capture) must
    not silently grow the fixture file with a stale duplicate that
    ReplayClient's first-match-wins replay would then shadow behind the
    newer entry -- see jobsniffer.http.fixtures.write_fixtures."""
    har_path = tmp_path / "site.har"
    body = base64.b64encode(b"{}").decode("ascii")
    _write_har(har_path, [_har_entry(url="https://example.com/jobs", body_text=body)])
    out_path = tmp_path / "fixtures.jsonl"

    extract_fixtures_from_har(har_path, ["example.com/jobs"], out_path)
    extract_fixtures_from_har(har_path, ["example.com/jobs"], out_path)

    assert len(load_fixtures(out_path)) == 1


def test_rerunning_with_a_different_har_replaces_the_matching_entry(tmp_path):
    body_v1 = base64.b64encode(b'{"v": 1}').decode("ascii")
    body_v2 = base64.b64encode(b'{"v": 2}').decode("ascii")

    har_v1 = tmp_path / "v1.har"
    _write_har(har_v1, [_har_entry(url="https://example.com/jobs", body_text=body_v1)])
    har_v2 = tmp_path / "v2.har"
    _write_har(har_v2, [_har_entry(url="https://example.com/jobs", body_text=body_v2)])
    out_path = tmp_path / "fixtures.jsonl"

    extract_fixtures_from_har(har_v1, ["example.com/jobs"], out_path)
    extract_fixtures_from_har(har_v2, ["example.com/jobs"], out_path)

    exchanges = load_fixtures(out_path)
    assert len(exchanges) == 1
    assert exchanges[0].content == b'{"v": 2}'


def test_status_code_and_headers_are_preserved(tmp_path):
    har_path = tmp_path / "site.har"
    body = base64.b64encode(b"{}").decode("ascii")
    _write_har(
        har_path,
        [
            _har_entry(
                url="https://example.com/jobs",
                status=404,
                body_text=body,
                response_headers={"content-type": "application/json"},
            )
        ],
    )
    out_path = tmp_path / "fixtures.jsonl"

    extract_fixtures_from_har(har_path, ["example.com/jobs"], out_path)

    exchanges = load_fixtures(out_path)
    assert exchanges[0].status_code == 404
    assert exchanges[0].headers == {"content-type": "application/json"}


def test_main_cli_wires_args_to_extract_fixtures_from_har(tmp_path, monkeypatch, capsys):
    import sys

    from scripts.har_to_fixtures import main

    har_path = tmp_path / "site.har"
    body = base64.b64encode(b'{"ok": true}').decode("ascii")
    _write_har(har_path, [_har_entry(url="https://example.com/jobs", body_text=body)])
    out_path = tmp_path / "fixtures.jsonl"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "har_to_fixtures",
            "--har",
            str(har_path),
            "--url-contains",
            "example.com/jobs",
            "--out",
            str(out_path),
        ],
    )

    main()

    assert len(load_fixtures(out_path)) == 1
    assert "Wrote 1 exchange(s)" in capsys.readouterr().out
