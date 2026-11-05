import json
import unittest

from career_engine.extract import (
    enrich_public_page, parse_jobposting, safe_public_url, robots_allowed
)
from career_engine.__main__ import select


class Response:
    def __init__(self, status=200, text="", content_type="text/html", location=""):
        self.status_code = status
        self.text = text
        self.headers = {"Content-Type": content_type}
        if location:
            self.headers["Location"] = location


class Session:
    def __init__(self, urls):
        self.urls = urls
        self.seen = []

    def get(self, url, **kwargs):
        self.seen.append(url)
        return self.urls[url]


class EnrichmentTests(unittest.TestCase):
    def test_parses_jobposting_schema(self):
        data = {"@context": "https://schema.org", "@type": "JobPosting",
                "title": "Special Assistant, Policy",
                "hiringOrganization": {"name": "Example Institute"},
                "description": "<p>Research and event programming</p>",
                "jobLocation": {"address": {"addressLocality": "Washington", "addressRegion": "DC"}},
                "datePosted": "2026-11-04"}
        html = '<script type="application/ld+json">' + json.dumps(data) + "</script>"
        result = parse_jobposting(html)
        self.assertEqual(result["company"], "Example Institute")
        self.assertEqual(result["location"], "Washington, DC")
        self.assertEqual(result["description"], "Research and event programming")

    def test_graph_and_remote(self):
        data = {"@graph": [{"@type": "Organization", "name": "Elsewhere"},
                           {"@type": ["JobPosting"], "title": "Program Associate",
                            "jobLocationType": "TELECOMMUTE"}]}
        result = parse_jobposting('<script type="application/ld+json">' + json.dumps(data) + "</script>")
        self.assertIn("Remote", result["location"])

    def test_no_jobposting(self):
        self.assertIsNone(parse_jobposting("<h1>Careers</h1>"))

    def test_private_destinations_blocked(self):
        for url in ("http://jobs.example.org/x", "https://127.0.0.1/admin",
                    "https://localhost/x", "https://admin:pw@example.org/private",
                    "https://company.internal/path"):
            with self.subTest(url=url):
                self.assertFalse(safe_public_url(url))

    def test_robots_disallow(self):
        url = "https://example.org/jobs/123"
        session = Session({"https://example.org/robots.txt": Response(text="User-agent: *\nDisallow: /jobs/")})
        self.assertFalse(robots_allowed(session, url, {}))

    def test_fetch_job_and_robots(self):
        url = "https://example.org/jobs/123"
        data = {"@type": "JobPosting", "title": "Policy Associate", "hiringOrganization": {"name": "Test"}}
        html = '<script type="application/ld+json">' + json.dumps(data) + "</script>"
        session = Session({"https://example.org/robots.txt": Response(status=404),
                           url: Response(text=html)})
        fields, status = enrich_public_page(session, url, "Policy Associate")
        self.assertEqual(status, "jobposting_metadata")
        self.assertEqual(fields["company"], "Test")

    def test_refuse_private_redirect(self):
        url = "https://example.org/job"
        session = Session({"https://example.org/robots.txt": Response(status=404),
                           url: Response(status=302, location="https://127.0.0.1/internal")})
        _, status = enrich_public_page(session, url)
        self.assertEqual(status, "unsafe_url")

    def test_missing_company_does_not_trigger_company_cap(self):
        jobs = [dict(title="Research Associate " + str(i),
                     company="", score=80-i, lane="research") for i in range(5)]
        self.assertEqual(len(select(jobs, 5)), 3)  # Lane cap, not blank employer


if __name__ == "__main__":
    unittest.main()
