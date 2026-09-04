from __future__ import annotations

import re

import numpy as np
from markdownify import markdownify as md

from jobsniffer.http import CurlCffiClient, HttpClient
from jobsniffer.logging_config import create_logger, set_logger_level  # noqa: F401
from jobsniffer.model import CompensationInterval, JobType, Site

# create_logger/set_logger_level are re-exported above for the 8 scrapers
# that already do `from jobsniffer.util import create_logger`; see
# jobsniffer.logging_config's module docstring for why the implementation
# lives there instead of here.


def create_session(
    *,
    proxies: list[str] | str | None = None,
    ca_cert: str | None = None,
    is_tls: bool = True,
    has_retry: bool = False,
    delay: int = 1,
    clear_cookies: bool = False,
) -> HttpClient:
    """Creates an HttpClient (CurlCffiClient) with TLS/JA3 impersonation,
    proxy rotation, and retry-with-backoff.

    `is_tls` and `clear_cookies` are accepted for call-site compatibility
    with the pre-curl_cffi signature but are true no-ops: curl_cffi always
    impersonates a browser TLS fingerprint (that's the reason it replaced
    tls_client/requests -- see jobsniffer.http.curl_client's module
    docstring), and per-request cookie clearing isn't meaningful for a
    client that doesn't persist a cookie jar across calls the way
    requests.Session does.

    `has_retry`/`delay` ARE forwarded, not discarded, though the mapping is
    a reinterpretation rather than an exact translation: CurlCffiClient
    always retries transient failures now (unlike the old tls_client path,
    which had none), so there's no literal "retry on/off" to preserve.
    Instead, callers that previously asked for has_retry=True with a
    slower `delay` (e.g. linkedin/naukri/bdjobs pass delay=5, vs.
    bayt/glassdoor's default delay=1) get a larger retry budget
    (max_attempts=5 -- a deliberately generous round number, not a precise
    translation of the old urllib3 Retry(total=3) math) and a longer
    initial backoff (wait_initial=delay), which honors the same *relative*
    intent (this scraper wants more patience than the default) rather than
    silently collapsing every scraper onto one identical retry policy.
    :return: An HttpClient
    """
    del is_tls, clear_cookies  # true no-ops, see above
    return CurlCffiClient(
        proxies=proxies,
        ca_cert=ca_cert,
        max_attempts=5 if has_retry else 3,
        wait_initial=float(delay) if has_retry else 0.5,
    )


def markdown_converter(description_html: str):
    if description_html is None:
        return None
    markdown = md(description_html)
    return markdown.strip()

def plain_converter(decription_html:str):
    from bs4 import BeautifulSoup
    if decription_html is None:
        return None
    soup = BeautifulSoup(decription_html, "html.parser")
    text = soup.get_text(separator=" ")
    text = re.sub(r'\s+',' ',text)
    return text.strip()


def extract_emails_from_text(text: str) -> list[str] | None:
    if not text:
        return None
    email_regex = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
    return email_regex.findall(text)


def get_enum_from_job_type(job_type_str: str) -> JobType | None:
    """
    Given a string, returns the corresponding JobType enum member if a match is found.
    """
    res = None
    for job_type in JobType:
        if job_type_str in job_type.value:
            res = job_type
    return res


def currency_parser(cur_str):
    # Remove any non-numerical characters
    # except for ',' '.' or '-' (e.g. EUR)
    cur_str = re.sub("[^-0-9.,]", "", cur_str)
    # Remove any 000s separators (either , or .)
    cur_str = re.sub("[.,]", "", cur_str[:-3]) + cur_str[-3:]

    if "." in list(cur_str[-3:]):
        num = float(cur_str)
    elif "," in list(cur_str[-3:]):
        num = float(cur_str.replace(",", "."))
    else:
        num = float(cur_str)

    return np.round(num, 2)


def remove_attributes(tag):
    for attr in list(tag.attrs):
        del tag[attr]
    return tag


def extract_salary(
    salary_str,
    lower_limit=1000,
    upper_limit=700000,
    hourly_threshold=350,
    monthly_threshold=30000,
    enforce_annual_salary=False,
):
    """
    Extracts salary information from a string and returns the salary interval, min and max salary values, and currency.
    (TODO: Needs test cases as the regex is complicated and may not cover all edge cases)
    """
    if not salary_str:
        return None, None, None, None

    annual_max_salary = None
    min_max_pattern = r"\$(\d+(?:,\d+)?(?:\.\d+)?)([kK]?)\s*[-—–]\s*(?:\$)?(\d+(?:,\d+)?(?:\.\d+)?)([kK]?)"

    def to_int(s):
        return int(float(s.replace(",", "")))

    def convert_hourly_to_annual(hourly_wage):
        return hourly_wage * 2080

    def convert_monthly_to_annual(monthly_wage):
        return monthly_wage * 12

    match = re.search(min_max_pattern, salary_str)

    if match:
        min_salary = to_int(match.group(1))
        max_salary = to_int(match.group(3))
        # Handle 'k' suffix for min and max salaries independently
        if "k" in match.group(2).lower() or "k" in match.group(4).lower():
            min_salary *= 1000
            max_salary *= 1000

        # Convert to annual if less than the hourly threshold
        if min_salary < hourly_threshold:
            interval = CompensationInterval.HOURLY.value
            annual_min_salary = convert_hourly_to_annual(min_salary)
            if max_salary < hourly_threshold:
                annual_max_salary = convert_hourly_to_annual(max_salary)

        elif min_salary < monthly_threshold:
            interval = CompensationInterval.MONTHLY.value
            annual_min_salary = convert_monthly_to_annual(min_salary)
            if max_salary < monthly_threshold:
                annual_max_salary = convert_monthly_to_annual(max_salary)

        else:
            interval = CompensationInterval.YEARLY.value
            annual_min_salary = min_salary
            annual_max_salary = max_salary

        # Ensure salary range is within specified limits
        if not annual_max_salary:
            return None, None, None, None
        if (
            lower_limit <= annual_min_salary <= upper_limit
            and lower_limit <= annual_max_salary <= upper_limit
            and annual_min_salary < annual_max_salary
        ):
            if enforce_annual_salary:
                return interval, annual_min_salary, annual_max_salary, "USD"
            else:
                return interval, min_salary, max_salary, "USD"
    return None, None, None, None


def extract_job_type(description: str):
    if not description:
        return []

    keywords = {
        JobType.FULL_TIME: r"full\s?time",
        JobType.PART_TIME: r"part\s?time",
        JobType.INTERNSHIP: r"internship",
        JobType.CONTRACT: r"contract",
    }

    listing_types = []
    for key, pattern in keywords.items():
        if re.search(pattern, description, re.IGNORECASE):
            listing_types.append(key)

    return listing_types if listing_types else None


def map_str_to_site(site_name: str) -> Site:
    return Site[site_name.upper()]


def get_enum_from_value(value_str):
    for job_type in JobType:
        if value_str in job_type.value:
            return job_type
    raise Exception(f"Invalid job type: {value_str}")


def convert_to_annual(job_data: dict):
    if job_data["interval"] == "hourly":
        job_data["min_amount"] *= 2080
        job_data["max_amount"] *= 2080
    if job_data["interval"] == "monthly":
        job_data["min_amount"] *= 12
        job_data["max_amount"] *= 12
    if job_data["interval"] == "weekly":
        job_data["min_amount"] *= 52
        job_data["max_amount"] *= 52
    if job_data["interval"] == "daily":
        job_data["min_amount"] *= 260
        job_data["max_amount"] *= 260
    job_data["interval"] = "yearly"


desired_order = [
    "id",
    "site",
    "job_url",
    "job_url_direct",
    "title",
    "company",
    "location",
    "date_posted",
    "job_type",
    "salary_source",
    "interval",
    "min_amount",
    "max_amount",
    "currency",
    "is_remote",
    "job_level",
    "job_function",
    "listing_type",
    "emails",
    "description",
    "company_industry",
    "company_url",
    "company_logo",
    "company_url_direct",
    "company_addresses",
    "company_num_employees",
    "company_revenue",
    "company_description",
    # naukri-specific fields
    "skills",
    "experience_range",
    "company_rating",
    "company_reviews_count",
    "vacancy_count",
    "work_from_home_type",
]
