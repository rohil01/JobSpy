"""jobsniffer.indeed.search

Step 1 of Indeed's two-step flow: GET the HTML search results page and
parse job identity + snippet + estimated salary out of it via
jobsniffer.indeed.parse. See jobsniffer.indeed.parse's module docstring
for why this alone is insufficient (truncated description, estimated
salary) and jobsniffer.indeed.detail for step 2.
"""

from __future__ import annotations

from dataclasses import dataclass

from jobsniffer.http.protocol import HttpClient
from jobsniffer.indeed.parse import extract_provider_data, results_from_provider_data


@dataclass(frozen=True, slots=True)
class SearchPageResult:
    """`blocked` distinguishes two causes that both look like "no jobs
    from this page" but call for different responses from the caller:

    - blocked=True: a non-2xx response, OR the page rendered without the
      mosaic-provider-jobcards marker at all (block page, CAPTCHA, or a
      markup change) -- Indeed.scrape() should fall back to GraphQL.
    - blocked=False, results=[]: the page rendered normally and the
      provider was present, it just has no results -- a genuinely narrow
      search term returning nothing. Falling back to GraphQL here would
      burn a request against a fragile shared-credential path for no
      reason (see jobsniffer.indeed.graphql's module docstring).
    """

    results: list[dict]
    blocked: bool


def build_search_params(
    *,
    search_term: str | None,
    location: str | None,
    distance: int | None,
    hours_old: int | None,
    start: int = 0,
) -> dict:
    params: dict = {"q": search_term, "l": location, "start": start}
    if distance is not None:
        params["radius"] = distance
    # Intentionally NOT defaulted to DEFAULT_DISTANCE_MILES when None, unlike
    # jobsniffer.indeed.graphql.fetch_page: omitting the query param is a
    # valid request here (Indeed applies its own server-side default), so
    # there's no bug to work around the way there was in the GraphQL query's
    # string interpolation. ScraperInput.distance now defaults to
    # DEFAULT_DISTANCE_MILES itself, so an *unset* distance already resolves
    # to 50 before it gets here -- this None branch is now reached only by a
    # caller *explicitly* passing distance=None to opt out of a radius
    # entirely. In that narrow case, the two Indeed paths can still apply a
    # different effective radius (HTML omits the param and gets Indeed's own
    # server-side default; GraphQL substitutes DEFAULT_DISTANCE_MILES,
    # since its query string has no "omit this field" form) -- accepted,
    # since a mid-scrape HTML->GraphQL fallback is already an unusual path
    # (see jobsniffer.indeed.__init__.Indeed.scrape).
    if hours_old is not None:
        # Indeed's HTML search accepts fromage in whole days.
        params["fromage"] = max(hours_old // 24, 1)
    return {k: v for k, v in params.items() if v is not None}


def fetch_search_page(
    session: HttpClient,
    *,
    base_url: str,
    search_term: str | None,
    location: str | None,
    distance: int | None,
    hours_old: int | None,
    start: int = 0,
    timeout: float = 15.0,
) -> SearchPageResult:
    """Fetches one page of Indeed search results.

    Never raises on a non-2xx/3xx response or an unparseable page -- a
    single blocked/CAPTCHA'd page shouldn't crash the whole scrape; the
    caller decides whether to fall back to the GraphQL path based on
    SearchPageResult.blocked.
    """
    params = build_search_params(
        search_term=search_term,
        location=location,
        distance=distance,
        hours_old=hours_old,
        start=start,
    )
    response = session.get(f"{base_url}/jobs", params=params, timeout=timeout)
    if not response.ok:
        return SearchPageResult(results=[], blocked=True)
    provider_data = extract_provider_data(response.text, "mosaic-provider-jobcards")
    if provider_data is None:
        return SearchPageResult(results=[], blocked=True)
    return SearchPageResult(
        results=results_from_provider_data(provider_data), blocked=False
    )
