"""jobsniffer.indeed.graphql

Fallback path when the mosaic HTML search (jobsniffer.indeed.search)
returns no results -- e.g. Indeed serves a block/CAPTCHA page to the HTML
path but the mobile-app GraphQL API still authenticates. Confirmed live
before wiring this in (see docs/2026-07-27-jobsniffer-modernization-plan.md,
Phase 3a): the shared indeed-api-key still returns HTTP 200 with real job
data as of this writing. It's a shared credential with no expiry contract,
so this path can stop working at any time without warning -- that's the
whole reason it's a fallback, not the primary path.

This is the pre-existing upstream JobSpy logic (jobsniffer.indeed.constant's
job_search_query/api_headers, jobsniffer.indeed.util's GraphQL-shaped
helpers), lightly adapted to be callable as a fallback rather than being
the Indeed class's only path.
"""

from __future__ import annotations

from datetime import UTC, datetime

from jobsniffer.http.protocol import HttpClient
from jobsniffer.indeed.constant import api_headers, job_search_query
from jobsniffer.indeed.util import get_compensation, get_job_type
from jobsniffer.indeed.util import is_job_remote as is_job_remote_graphql
from jobsniffer.model import DEFAULT_DISTANCE_MILES, DescriptionFormat, JobPost, JobType, Location
from jobsniffer.util import extract_emails_from_text, markdown_converter


def build_filters(
    *, hours_old: int | None, easy_apply: bool | None, job_type: JobType | None, is_remote: bool
) -> str:
    """Composes the GraphQL `filters` block. Only one of
    hours_old/job_type+is_remote/easy_apply can be expressed at once --
    an upstream JobSpy limitation carried over unchanged since it's a
    real constraint of Indeed's GraphQL filter schema, not an artifact of
    the old transport."""
    if hours_old:
        return f"""
            filters: {{
                date: {{
                  field: "dateOnIndeed",
                  start: "{hours_old}h"
                }}
            }}
            """
    if easy_apply:
        return """
            filters: {
                keyword: {
                  field: "indeedApplyScope",
                  keys: ["DESKTOP"]
                }
            }
            """
    if job_type or is_remote:
        job_type_key_mapping = {
            JobType.FULL_TIME: "CF3CP",
            JobType.PART_TIME: "75GKK",
            JobType.CONTRACT: "NJXCK",
            JobType.INTERNSHIP: "VDTG7",
        }
        keys = []
        if job_type:
            keys.append(job_type_key_mapping[job_type])
        if is_remote:
            keys.append("DSQF7")
        if keys:
            keys_str = '", "'.join(keys)
            return f"""
                filters: {{
                  composite: {{
                    filters: [{{
                      keyword: {{
                        field: "attributes",
                        keys: ["{keys_str}"]
                      }}
                    }}]
                  }}
                }}
                """
    return ""


def fetch_page(
    session: HttpClient,
    *,
    api_url: str,
    api_country_code: str,
    search_term: str | None,
    location: str | None,
    distance: int | None,
    hours_old: int | None,
    easy_apply: bool | None,
    job_type: JobType | None,
    is_remote: bool,
    cursor: str | None,
    timeout: int = 10,
) -> tuple[list[dict], str | None]:
    """Fetches one page of GraphQL results. Returns ([], None) on a
    non-2xx response -- the caller (Indeed.scrape's fallback branch)
    already has nothing better to fall back to, so this just stops.

    Live-caught bug (reproduced against the real API, not just inferred):
    distance=None -- ScraperInput's actual default, despite the project's
    own README documenting "distance (int): in miles, default 50" -- used
    to render as the literal string `radius: None` in the GraphQL query
    text, which the API silently rejects as an empty/errored jobSearch
    rather than raising, making every default-distance search return zero
    results through this fallback. Confirmed live: identical query with
    `radius: 50` returns 100 results with no errors.
    """
    search_term_escaped = search_term.replace('"', '\\"') if search_term else ""
    effective_distance = distance if distance is not None else DEFAULT_DISTANCE_MILES
    query = job_search_query.format(
        what=(f'what: "{search_term_escaped}"' if search_term_escaped else ""),
        location=(
            f'location: {{where: "{location}", radius: {effective_distance}, radiusUnit: MILES}}'
            if location
            else ""
        ),
        dateOnIndeed=hours_old,
        cursor=f'cursor: "{cursor}"' if cursor else "",
        filters=build_filters(
            hours_old=hours_old, easy_apply=easy_apply, job_type=job_type, is_remote=is_remote
        ),
    )
    headers = api_headers.copy()
    headers["indeed-co"] = api_country_code
    response = session.post(
        api_url, headers=headers, json={"query": query}, timeout=timeout, verify=False
    )
    if not response.ok:
        return [], None
    data = response.json()
    job_search = data.get("data", {}).get("jobSearch")
    if not job_search:
        return [], None
    results = job_search.get("results", [])
    next_cursor = job_search.get("pageInfo", {}).get("nextCursor")
    return results, next_cursor


def job_post_from_graphql_result(
    job: dict, *, base_url: str, description_format: DescriptionFormat | None
) -> JobPost:
    """Adapted from the pre-rewrite Indeed._process_job -- same field
    mapping, just parameterized instead of reading self.*."""
    job_url = f'{base_url}/viewjob?jk={job["key"]}'
    description = job["description"]["html"]
    if description_format == DescriptionFormat.MARKDOWN:
        description = markdown_converter(description)

    job_type = get_job_type(job["attributes"])
    timestamp_seconds = job["datePublished"] / 1000
    date_posted = datetime.fromtimestamp(timestamp_seconds, tz=UTC).date()
    employer = job["employer"].get("dossier") if job["employer"] else None
    employer_details = employer.get("employerDetails", {}) if employer else {}
    rel_url = job["employer"]["relativeCompanyPageUrl"] if job["employer"] else None
    return JobPost(
        id=f'in-{job["key"]}',
        title=job["title"],
        description=description,
        company_name=job["employer"].get("name") if job.get("employer") else None,
        company_url=(f"{base_url}{rel_url}" if job["employer"] else None),
        company_url_direct=(employer["links"]["corporateWebsite"] if employer else None),
        location=Location(
            city=job.get("location", {}).get("city"),
            state=job.get("location", {}).get("admin1Code"),
            country=job.get("location", {}).get("countryCode"),
        ),
        job_type=job_type,
        compensation=get_compensation(job["compensation"]),
        date_posted=date_posted,
        job_url=job_url,
        job_url_direct=(job["recruit"].get("viewJobUrl") if job.get("recruit") else None),
        emails=extract_emails_from_text(description) if description else None,
        is_remote=is_job_remote_graphql(job, description),
        company_addresses=(
            employer_details["addresses"][0] if employer_details.get("addresses") else None
        ),
        company_industry=(
            employer_details["industry"].replace("Iv1", "").replace("_", " ").title().strip()
            if employer_details.get("industry")
            else None
        ),
        company_num_employees=employer_details.get("employeesLocalizedLabel"),
        company_revenue=employer_details.get("revenueLocalizedLabel"),
        company_description=employer_details.get("briefDescription"),
        company_logo=(
            employer["images"].get("squareLogoUrl")
            if employer and employer.get("images")
            else None
        ),
    )
