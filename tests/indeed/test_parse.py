import json
from datetime import date
from pathlib import Path

import pytest

from jobsniffer.http.fixtures import load_fixtures
from jobsniffer.indeed.parse import (
    compensation_from_extracted_salary,
    compensation_from_salary_info_model,
    date_posted_from_epoch_millis,
    extract_detail_description,
    extract_provider_data,
    is_job_remote,
    job_types_from_taxonomy,
    parse_search_results,
)
from jobsniffer.model import CompensationInterval, JobType

FIXTURES = Path(__file__).parent.parent / "fixtures" / "indeed.jsonl"


@pytest.fixture(scope="module")
def exchanges():
    return load_fixtures(FIXTURES)


@pytest.fixture(scope="module")
def search_html(exchanges):
    return exchanges[0].content.decode("utf-8")


@pytest.fixture(scope="module")
def detail_body_for_known_job(exchanges):
    """The exchange for jk=20d6afbf6595234e -- known ground truth from the
    original HAR analysis: title "Website Developer & Digital Marketing",
    a 7259-char description, salaryMin/Max 60000/65000 USD."""
    for exchange in exchanges:
        if "jk=20d6afbf6595234e" in exchange.url:
            return json.loads(exchange.content)["body"]
    raise AssertionError("known-value fixture entry not found")


def test_extract_provider_data_finds_jobcards_provider(search_html):
    data = extract_provider_data(search_html, "mosaic-provider-jobcards")
    assert data is not None
    assert "metaData" in data


def test_extract_provider_data_returns_none_for_missing_provider(search_html):
    assert extract_provider_data(search_html, "mosaic-provider-does-not-exist") is None


def test_extract_provider_data_returns_none_for_empty_html():
    assert extract_provider_data("<html></html>", "mosaic-provider-jobcards") is None


def test_extract_provider_data_raises_on_unbalanced_json():
    broken_html = 'window.mosaic.providerData["mosaic-provider-jobcards"]={"a": 1'
    # json.JSONDecoder().raw_decode surfaces its own JSONDecodeError (a
    # ValueError subclass) for malformed JSON -- no custom exception
    # needed, and no custom message to keep in sync with stdlib's wording.
    with pytest.raises(json.JSONDecodeError):
        extract_provider_data(broken_html, "mosaic-provider-jobcards")


def test_parse_search_results_returns_all_40_known_jobs(search_html):
    results = parse_search_results(search_html)
    assert len(results) == 40


def test_parse_search_results_empty_when_provider_absent():
    assert parse_search_results("<html>no mosaic data here</html>") == []


def test_parse_search_results_includes_the_known_job_key(search_html):
    results = parse_search_results(search_html)
    keys = {job["jobkey"] for job in results}
    assert "20d6afbf6595234e" in keys


def test_search_result_snippet_is_truncated_not_full_description(search_html):
    """Documents the core Phase 3a finding: search results carry a
    snippet, not the full description -- this is why a detail fetch is
    mandatory, not optional."""
    results = parse_search_results(search_html)
    for job in results:
        assert len(job.get("snippet", "")) < 7259


def test_job_types_from_taxonomy_maps_full_time():
    taxonomy = [
        {"attributes": [{"label": "Full-time", "suid": "CF3CP"}], "label": "job-types"},
        {"attributes": [], "label": "shifts"},
    ]
    assert job_types_from_taxonomy(taxonomy) == [JobType.FULL_TIME]


def test_job_types_from_taxonomy_empty_when_no_job_types_group():
    assert job_types_from_taxonomy([{"attributes": [], "label": "shifts"}]) == []


def test_job_types_from_taxonomy_handles_none():
    assert job_types_from_taxonomy(None) == []


def test_compensation_from_extracted_salary_known_job():
    job = {
        "extractedSalary": {"max": 100000, "min": 85000, "type": "YEARLY"},
        "salarySnippet": {"currency": "USD"},
    }
    comp = compensation_from_extracted_salary(job)
    assert comp.min_amount == 85000
    assert comp.max_amount == 100000
    assert comp.currency == "USD"
    assert comp.interval == CompensationInterval.YEARLY


def test_compensation_from_extracted_salary_none_when_absent():
    assert compensation_from_extracted_salary({}) is None


def test_compensation_from_extracted_salary_none_when_min_and_max_both_null():
    job = {"extractedSalary": {"min": None, "max": None, "type": "YEARLY"}}
    assert compensation_from_extracted_salary(job) is None


def test_compensation_from_salary_info_model_matches_known_job(detail_body_for_known_job):
    comp = compensation_from_salary_info_model(detail_body_for_known_job["salaryInfoModel"])
    assert comp.min_amount == 60000
    assert comp.max_amount == 65000
    assert comp.currency == "USD"
    assert comp.interval == CompensationInterval.YEARLY


def test_compensation_from_salary_info_model_none_when_missing():
    assert compensation_from_salary_info_model(None) is None
    assert compensation_from_salary_info_model({}) is None


def test_compensation_from_salary_info_model_none_when_min_and_max_both_null():
    salary_info = {"salaryMin": None, "salaryMax": None, "salaryType": "YEARLY"}
    assert compensation_from_salary_info_model(salary_info) is None


def test_date_posted_from_epoch_millis():
    # 1784178000000 ms -> a known search-result pubDate, verified via
    # datetime.fromtimestamp(1784178000000/1000, tz=UTC).date() directly
    # before writing this assertion.
    assert date_posted_from_epoch_millis(1784178000000) == date(2026, 7, 16)


def test_date_posted_from_epoch_millis_none_passthrough():
    assert date_posted_from_epoch_millis(None) is None


def test_is_job_remote_true_for_remote_location_flag():
    assert is_job_remote({"remoteLocation": True}) is True


def test_is_job_remote_true_for_remote_keyword_in_title():
    assert is_job_remote({"remoteLocation": False, "title": "Remote Software Engineer"}) is True


def test_is_job_remote_false_otherwise():
    assert is_job_remote({"remoteLocation": False, "title": "Marketing Manager", "formattedLocation": "Boston, MA"}) is False


def test_extract_detail_description_matches_known_job(detail_body_for_known_job):
    description = extract_detail_description(detail_body_for_known_job)
    assert description is not None
    assert len(description) == 7259
    assert description.startswith("<p><b>About Emmaty</b></p>")


def test_extract_detail_description_falls_back_to_mosaic_mirror():
    body = {
        "jobInfoWrapperModel": {"jobInfoModel": {}},
        "mosaicData": {
            "serverContextData": {
                "request": {
                    "data": {
                        "metaData": {
                            "js-match-insights-provider": {
                                "jobDescription": "<p>fallback description</p>"
                            }
                        }
                    }
                }
            }
        },
    }
    assert extract_detail_description(body) == "<p>fallback description</p>"


def test_extract_detail_description_none_when_neither_field_present():
    assert extract_detail_description({}) is None
