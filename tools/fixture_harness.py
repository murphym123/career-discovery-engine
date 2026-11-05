"""Offline transport for synthetic, documentation-shaped provider fixtures."""
import copy
import json
from pathlib import Path
from urllib.parse import urlsplit
import requests

FIXTURE = Path(__file__).resolve().parents[1] / "tests/fixtures/discovery.json"
TEST_ENV = {"MUSE_API_KEY": "synthetic-muse-key", "RELIEFWEB_APPNAME": "synthetic-approved-app",
            "USAJOBS_API_KEY": "synthetic-usa-key", "USAJOBS_USER_AGENT": "tester@example.org"}


def load_fixture():
    return json.loads(FIXTURE.read_text())


class Response:
    def __init__(self, data=None, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return copy.deepcopy(self.data)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FixtureSession:
    def __init__(self, fixture=None):
        self.fixture = copy.deepcopy(fixture or load_fixture())
        self.calls = []
        self.offsets = {}

    def get(self, url, **kwargs):
        host = urlsplit(url).hostname
        source = {"remotive.com": "remotive", "jobicy.com": "jobicy",
                  "www.themuse.com": "muse", "api.reliefweb.int": "reliefweb",
                  "data.usajobs.gov": "usajobs"}.get(host)
        if source is None:
            raise AssertionError(f"Unexpected network request: {host}")
        self.calls.append((source, copy.deepcopy(kwargs)))
        index = self.offsets.get(source, 0)
        self.offsets[source] = index + 1
        pages = self.fixture[source]
        if index >= len(pages):
            raise AssertionError(f"Unexpected extra page for {source}")
        result = pages[index]
        if isinstance(result, Exception):
            raise result
        return result if isinstance(result, Response) else Response(result)
