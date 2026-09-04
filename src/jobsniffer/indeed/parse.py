"""jobsniffer.indeed.parse

Pure parsing functions for Indeed's two-step HTML flow (see
docs/2026-07-27-jobsniffer-modernization-plan.md, Phase 3a):

1. Search: GET /jobs?... returns an HTML page with job identity, a
   *truncated* description snippet, and an estimated salary, embedded as
   window.mosaic.providerData["mosaic-provider-jobcards"] = {...} --
   confirmed against tests/fixtures/indeed.jsonl: the page carries 40
   jobs with `snippet` (truncated) and `salarySnippet`/`extractedSalary`
   (estimates), but exactly one `sanitizedJobDescription` -- the job open
   in the detail pane, not all 40.
2. Detail: GET /viewjob?jk={jobkey}&from=vjs returns JSON with the full
   `sanitizedJobDescription` and an authoritative `salaryInfoModel`.

Neither step alone has both identity and full content -- this module has
no HTTP dependency; jobsniffer.indeed.search/detail call it after making
the actual request.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from jobsniffer.model import Compensation, CompensationInterval, JobType
from jobsniffer.util import get_enum_from_job_type

_INTERVAL_BY_SALARY_TYPE = {
    "YEARLY": CompensationInterval.YEARLY,
    "MONTHLY": CompensationInterval.MONTHLY,
    "WEEKLY": CompensationInterval.WEEKLY,
    "DAILY": CompensationInterval.DAILY,
    "HOURLY": CompensationInterval.HOURLY,
}


def extract_provider_data(html: str, provider_key: str) -> dict | None:
    """Extracts and parses the JSON object assigned to
    window.mosaic.providerData["<provider_key>"] in an Indeed search page.

    Returns None if the marker isn't present -- the page didn't render
    that provider (blocked, CAPTCHA, markup change, or a provider that
    genuinely has no data on this page), which callers must treat as "no
    data available", not synthesize a fabricated empty result for (P8).
    Callers that need to distinguish "blocked" from "legitimately empty"
    (jobsniffer.indeed.search.fetch_search_page) rely on this None to mean
    the former -- parse_search_results collapses it to [] for callers that
    don't need the distinction.
    """
    marker = f'window.mosaic.providerData["{provider_key}"]='
    idx = html.find(marker)
    if idx == -1:
        return None
    start = idx + len(marker)
    # json.JSONDecoder().raw_decode parses from start_idx and reports where
    # it stopped, handling nested braces inside string values (e.g. an
    # HTML snippet field) correctly via its own string-aware tokenizer --
    # no need to hand-roll that scan. Verified against this project's real
    # fixture: identical output to a hand-rolled brace-counter, ~9x faster.
    data, _end_idx = json.JSONDecoder().raw_decode(html, start)
    return data


def results_from_provider_data(provider_data: dict) -> list[dict]:
    """Pulls the per-job `results` list out of an already-parsed
    mosaic-provider-jobcards payload. Split from parse_search_results so
    callers that already called extract_provider_data (e.g.
    jobsniffer.indeed.search.fetch_search_page, which needs the parsed
    payload itself to distinguish "blocked" from "empty") don't have to
    re-parse the whole page a second time to get the results list."""
    model = provider_data.get("metaData", {}).get("mosaicProviderJobCardsModel", {})
    return model.get("results", [])


def parse_search_results(html: str) -> list[dict]:
    """Returns the raw per-job dicts from mosaic-provider-jobcards's
    `results` list, or [] if the provider wasn't present on the page."""
    data = extract_provider_data(html, "mosaic-provider-jobcards")
    if data is None:
        return []
    return results_from_provider_data(data)


def job_types_from_taxonomy(taxonomy_attributes: list[dict] | None) -> list[JobType]:
    """Search results carry job type under
    taxonomyAttributes[i].attributes[j].label where the group's own
    `label` is "job-types" (e.g. "Full-time", "Part-time")."""
    if not taxonomy_attributes:
        return []
    job_types: list[JobType] = []
    for group in taxonomy_attributes:
        if group.get("label") != "job-types":
            continue
        for attribute in group.get("attributes", []):
            job_type = get_enum_from_job_type(
                attribute["label"].replace("-", "").replace(" ", "").lower()
            )
            if job_type:
                job_types.append(job_type)
    return job_types


def _compensation_from_amounts(
    *, min_amount: float | None, max_amount: float | None, salary_type: str, currency: str
) -> Compensation | None:
    """Shared by compensation_from_extracted_salary (search-result
    estimate) and compensation_from_salary_info_model (detail-fetch
    authoritative figure) -- both resolve to the same
    min/max/type/currency shape, just read from different field names."""
    if min_amount is None and max_amount is None:
        return None
    return Compensation(
        interval=_INTERVAL_BY_SALARY_TYPE.get(salary_type),
        min_amount=min_amount,
        max_amount=max_amount,
        currency=currency,
    )


def compensation_from_extracted_salary(job: dict) -> Compensation | None:
    """Search-result-level salary: an ESTIMATE (source: EXTRACTION), used
    only until the detail fetch's authoritative salaryInfoModel replaces
    it -- see compensation_from_salary_info_model."""
    extracted = job.get("extractedSalary")
    if not extracted:
        return None
    return _compensation_from_amounts(
        min_amount=extracted.get("min"),
        max_amount=extracted.get("max"),
        salary_type=extracted.get("type", ""),
        currency=job.get("salarySnippet", {}).get("currency", "USD"),
    )


def compensation_from_salary_info_model(salary_info: dict | None) -> Compensation | None:
    """Detail-fetch salary from /viewjob's salaryInfoModel -- the
    authoritative figure that should replace any search-result estimate."""
    if not salary_info:
        return None
    return _compensation_from_amounts(
        min_amount=salary_info.get("salaryMin"),
        max_amount=salary_info.get("salaryMax"),
        salary_type=salary_info.get("salaryType", ""),
        currency=salary_info.get("salaryCurrency", "USD"),
    )


def date_posted_from_epoch_millis(epoch_millis: int | None) -> date | None:
    """Search results carry `pubDate` as milliseconds since the epoch."""
    if epoch_millis is None:
        return None
    return datetime.fromtimestamp(epoch_millis / 1000, tz=UTC).date()


def is_job_remote(job: dict) -> bool:
    """Searches the search-result job dict's remote flag, title, and
    formatted location for remote signals. Does NOT look at the detail
    fetch's full description -- jobsniffer.indeed.__init__._build_job_post
    calls this with only the search-result dict, before/independent of
    whatever the detail fetch returns."""
    if job.get("remoteLocation"):
        return True
    remote_keywords = ("remote", "work from home", "wfh")
    haystack = f"{job.get('title', '')} {job.get('formattedLocation', '')}".lower()
    return any(keyword in haystack for keyword in remote_keywords)


def extract_detail_description(detail_body: dict) -> str | None:
    """The full job description from /viewjob's response. Two fields
    carry it (jobInfoModel.sanitizedJobDescription and the
    js-match-insights-provider mirror in mosaicData) -- confirmed
    identical content in tests/fixtures/indeed.jsonl (modulo whitespace);
    the primary field is used, the mirror only as a fallback if Indeed
    ever stops populating the primary one."""
    try:
        return detail_body["jobInfoWrapperModel"]["jobInfoModel"][
            "sanitizedJobDescription"
        ]
    except (KeyError, TypeError):
        pass
    try:
        return detail_body["mosaicData"]["serverContextData"]["request"]["data"][
            "metaData"
        ]["js-match-insights-provider"]["jobDescription"]
    except (KeyError, TypeError):
        return None
