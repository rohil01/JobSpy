from bs4 import BeautifulSoup

from jobsniffer.linkedin.util import job_id_from_search_card


def _card(html: str):
    return BeautifulSoup(html, "html.parser").find("div", class_="base-search-card")


def test_extracts_job_id_from_href():
    card = _card(
        """
        <div class="base-search-card">
            <a class="base-card__full-link"
               href="https://www.linkedin.com/jobs/view/software-engineer-at-acme-4412034983?refId=abc">
            </a>
        </div>
        """
    )
    assert job_id_from_search_card(card) == "4412034983"


def test_returns_none_when_link_tag_missing():
    card = _card('<div class="base-search-card"><span>no link here</span></div>')
    assert job_id_from_search_card(card) is None


def test_returns_none_when_href_attribute_missing():
    card = _card(
        '<div class="base-search-card">'
        '<a class="base-card__full-link">no href attribute</a>'
        "</div>"
    )
    assert job_id_from_search_card(card) is None
