from bs4 import BeautifulSoup
from bs4.element import Tag

from jobsniffer.model import JobType, Location
from jobsniffer.util import get_enum_from_job_type


def job_id_from_search_card(job_card: Tag) -> str | None:
    """Extracts the job id from a single `div.base-search-card` result on
    LinkedIn's jobs-guest search page.

    Shared by LinkedIn.scrape() and src/scripts/record_linkedin_fixtures.py
    (which seeds a fixture for a job the real scraper would actually
    visit) so the two can't silently drift apart -- previously each had
    its own copy of this parse.
    """
    href_tag = job_card.find("a", class_="base-card__full-link")
    href_value = href_tag.attrs.get("href") if href_tag else None
    if not isinstance(href_value, str):
        return None
    href = href_value.split("?")[0]
    return href.split("-")[-1]


def job_type_code(job_type_enum: JobType) -> str:
    return {
        JobType.FULL_TIME: "F",
        JobType.PART_TIME: "P",
        JobType.INTERNSHIP: "I",
        JobType.CONTRACT: "C",
        JobType.TEMPORARY: "T",
    }.get(job_type_enum, "")


def parse_job_type(soup_job_type: BeautifulSoup) -> list[JobType] | None:
    """
    Gets the job type from job page
    :param soup_job_type:
    :return: JobType
    """
    h3_tag = soup_job_type.find(
        "h3",
        class_="description__job-criteria-subheader",
        string=lambda text: "Employment type" in text,
    )
    employment_type = None
    if h3_tag:
        employment_type_span = h3_tag.find_next_sibling(
            "span",
            class_="description__job-criteria-text description__job-criteria-text--criteria",
        )
        if employment_type_span:
            employment_type = employment_type_span.get_text(strip=True)
            employment_type = employment_type.lower()
            employment_type = employment_type.replace("-", "")

    return [get_enum_from_job_type(employment_type)] if employment_type else []


def parse_job_level(soup_job_level: BeautifulSoup) -> str | None:
    """
    Gets the job level from job page
    :param soup_job_level:
    :return: str
    """
    h3_tag = soup_job_level.find(
        "h3",
        class_="description__job-criteria-subheader",
        string=lambda text: "Seniority level" in text,
    )
    job_level = None
    if h3_tag:
        job_level_span = h3_tag.find_next_sibling(
            "span",
            class_="description__job-criteria-text description__job-criteria-text--criteria",
        )
        if job_level_span:
            job_level = job_level_span.get_text(strip=True)

    return job_level


def parse_company_industry(soup_industry: BeautifulSoup) -> str | None:
    """
    Gets the company industry from job page
    :param soup_industry:
    :return: str
    """
    h3_tag = soup_industry.find(
        "h3",
        class_="description__job-criteria-subheader",
        string=lambda text: "Industries" in text,
    )
    industry = None
    if h3_tag:
        industry_span = h3_tag.find_next_sibling(
            "span",
            class_="description__job-criteria-text description__job-criteria-text--criteria",
        )
        if industry_span:
            industry = industry_span.get_text(strip=True)

    return industry


def is_job_remote(title: dict, description: str, location: Location) -> bool:
    """
    Searches the title, location, and description to check if job is remote
    """
    remote_keywords = ["remote", "work from home", "wfh"]
    location = location.display_location()
    full_string = f'{title} {description} {location}'.lower()
    is_remote = any(keyword in full_string for keyword in remote_keywords)
    return is_remote
