"""Shared test doubles for tests/indeed/*.

FakeResponse was independently redefined near-identically across
test_detail.py, test_search.py, test_graphql.py, and
test_scrape_integration.py -- consolidated here once instead.
"""

import json


class FakeResponse:
    def __init__(self, *, ok=True, text=""):
        self.ok = ok
        self.text = text

    def json(self):
        return json.loads(self.text)
