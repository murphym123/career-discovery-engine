"""Shortlist view: contract validation, untrusted-field handling, local feedback, UI."""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dashboard.shortlist import (
    WITHHELD, ShortlistError, link_source, feedback_path, filter_jobs, load_feedback, load_shortlist, save_feedback,
    shortlist_path,
)

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "fixtures" / "shortlist-demo" / "shortlist.json"


def record(**overrides):
    job = {
        "review_id": "r-1", "title": "Policy Analyst", "employer": "Synthetic Institute",
        "location": "Washington, DC", "work_arrangement": "Hybrid",
        "compensation_text": "$60,000–$70,000", "official_url": "https://example.org/jobs/1",
        "fit_reasons": ["Duties match survey analysis experience."],
        "gaps_to_verify": ["Writing sample?"], "review_priority": "prioritize",
        "availability_status": "verified_current", "availability_checked_at": "2026-11-14",
    }
    job.update(overrides)
    return job


class ShortlistContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.dir = Path(temporary.name)
        self.path = self.dir / "shortlist.json"

    def write(self, jobs, **document):
        self.path.write_text(json.dumps({"schema_version": 1, "jobs": jobs, **document}))
        return load_shortlist(self.path)

    def test_demo_fixture_is_synthetic_and_complete(self):
        shortlist = load_shortlist(DEMO)
        self.assertTrue(shortlist["demo"])
        self.assertGreaterEqual(len(shortlist["jobs"]), 20)
        for job in shortlist["jobs"]:
            self.assertTrue(job["official_url"].startswith("https://example.org/"))
            self.assertTrue(2 <= len(job["fit_reasons"]) <= 3)
        statuses = {job["availability_status"] for job in shortlist["jobs"]}
        self.assertEqual(statuses, {"verified_current", "not_yet_verified", "no_longer_open"})

    def test_missing_file_and_empty_list_are_empty_states(self):
        self.assertIsNone(load_shortlist(None))
        self.assertIsNone(load_shortlist(self.path))
        self.assertEqual(self.write([])["jobs"], [])

    def test_untrusted_fields_are_never_invented_or_passed_through_unsafely(self):
        job = self.write([record(
            official_url="javascript:alert(1)", application_url="https://example.org/a?token=x",
            title="Contact hr@private.example", fit_reasons=["ok", 42, "see https://evil.example"],
            review_priority="score=97", availability_status="verified_current",
            availability_checked_at="recently", compensation_text="",
        )])["jobs"][0]
        self.assertIsNone(job["official_url"])
        self.assertIsNone(job["application_url"])
        self.assertIsNone(job["title"])
        self.assertIsNone(job["compensation_text"])
        self.assertEqual(job["fit_reasons"], ["ok"])
        self.assertEqual(job["review_priority"], "unassessed")
        # A verification claim with no real check date is shown as not verified.
        self.assertEqual(job["availability_status"], "not_yet_verified")

    def test_exact_safe_posting_url_is_kept(self):
        url = "https://jobs.lever.co/example/abc-123?lever-origin=applied"
        job = self.write([record(official_url=url + "#apply")])["jobs"][0]
        self.assertEqual(job["official_url"], url)
        self.assertEqual(job["application_url"], url)
        self.assertEqual(job["availability_checked"], "Nov 14, 2026")

    def test_invalid_documents_raise(self):
        for content in ("not json", "[]", json.dumps({"schema_version": 2, "jobs": []}),
                        json.dumps({"schema_version": 1}),
                        json.dumps({"schema_version": 1, "jobs": [record(review_id="../x")]}),
                        json.dumps({"schema_version": 1, "jobs": [record(), record()]})):
            self.path.write_text(content)
            with self.assertRaises(ShortlistError, msg=content):
                load_shortlist(self.path)

    def test_filters_keep_file_order(self):
        jobs = self.write([
            record(review_id="a", review_priority="consider", availability_status="not_yet_verified"),
            record(review_id="b", employer="Transit Council"),
            record(review_id="c"),
        ])["jobs"]
        ids = lambda shown: [job["review_id"] for job in shown]
        self.assertEqual(ids(filter_jobs(jobs, priority=["prioritize"])), ["b", "c"])
        self.assertEqual(ids(filter_jobs(jobs, availability=["not_yet_verified"])), ["a"])
        self.assertEqual(ids(filter_jobs(jobs, search="transit  council")), ["b"])

    def test_first_real_shortlist_priority_and_availability_contract(self):
        jobs = self.write([
            record(review_id="first", review_priority="high",
                   availability_status="verified_current",
                   availability_checked_at="2026-11-15T07:51:13Z"),
            record(review_id="second", review_priority="medium",
                   availability_status="not_yet_verified",
                   availability_checked_at=None),
            record(review_id="third", review_priority="high",
                   availability_status="no_longer_open",
                   availability_checked_at="2026-11-15T07:51:13Z"),
        ])["jobs"]
        self.assertEqual([x["review_priority"] for x in jobs],
                         ["high", "medium", "high"])
        self.assertEqual([x["availability_status"] for x in jobs],
                         ["verified_current", "not_yet_verified", "no_longer_open"])
        self.assertEqual(len(filter_jobs(jobs, priority=["high"])), 2)
        self.assertEqual(len(filter_jobs(jobs, availability=["not_yet_verified"])), 1)

    def test_old_status_names_are_not_accepted(self):
        jobs = self.write([
            record(review_id="old-open", availability_status="unverified", availability_checked_at=None),
            record(review_id="old-closed", availability_status="closed"),
        ])["jobs"]
        self.assertEqual([x["availability_status"] for x in jobs], ["not_yet_verified"] * 2)

    def test_timezone_aware_check_times_are_shown_in_utc(self):
        job = self.write([record(availability_checked_at="2026-11-15T01:30:00-07:00")])["jobs"][0]
        self.assertEqual(job["availability_status"], "verified_current")
        self.assertEqual(job["availability_checked"], "Nov 15, 2026 at 08:30 UTC")
        naive = self.write([record(availability_checked_at="2026-11-15T01:30:00")])["jobs"][0]
        self.assertEqual(naive["availability_status"], "not_yet_verified")

    def test_listing_fields_are_shown_as_captured_or_marked_missing_or_withheld(self):
        job = self.write([record(listing={
            "provider": "greenhouse", "board_key": "civic", "source": "targeted_snapshot",
            "retrieved_at": "2026-11-14T18:02:11Z", "title": "Policy Analyst",
            "company": "Contact hr@private.example", "posted_at": "2026-11-01", "employment_type": "",
            "description_truncated": True, "omitted_urls": ["url"], "unexpected": "kept",
            "bad key!": "dropped",
            "description": ("Intro " * 400) + "\n\nApply at https://ats.example/apply or email "
                           "jobs@private.example\nNote: password: hunter2\nLast line.",
            "url": "https://boards.greenhouse.io/civic/jobs/42",
            "original_posting_url": "javascript:alert(1)",
        })])["jobs"][0]
        listing = job["listing"]
        self.assertEqual((listing["provider"], listing["board_key"], listing["title"]),
                         ("greenhouse", "civic", "Policy Analyst"))
        self.assertEqual(listing["company"], WITHHELD)  # redacted, not reported as missing
        self.assertIsNone(listing["employment_type"])
        self.assertIsNone(listing["location"])
        # The full description is kept; only unsafe words are redacted.
        description = listing["description"]
        self.assertTrue(description.startswith("Intro Intro"))
        self.assertTrue(description.endswith("Last line."))
        self.assertIn("Apply at [redacted] or email [redacted]", description)
        self.assertNotRegex(description, "https|private|hunter2")
        self.assertGreater(listing["description_length"], 2400)
        self.assertEqual((listing["description_truncated"], listing["omitted_urls"]), ("yes", "url"))
        self.assertEqual(listing["url"], "https://boards.greenhouse.io/civic/jobs/42")
        self.assertEqual(listing["original_posting_url"], WITHHELD)
        self.assertEqual(listing["extra"], {"unexpected": "kept"})
        self.assertIsNone(self.write([record()])["jobs"][0]["listing"])

    def test_link_source_names_known_boards(self):
        self.assertEqual(link_source("https://job-boards.greenhouse.io/x/jobs/1"),
                         ("Greenhouse", "job-boards.greenhouse.io"))
        self.assertEqual(link_source("https://careers.example.org/1"), (None, "careers.example.org"))
        self.assertEqual(link_source(None), (None, None))

    def test_path_resolution(self):
        self.assertIsNone(shortlist_path({}))
        self.assertEqual(shortlist_path({"PROSPECT_DATA_DIR": "/d"}), Path("/d/shortlist.json"))
        self.assertEqual(shortlist_path({"PROSPECT_DATA_DIR": "/d", "PROSPECT_SHORTLIST": "/s.json"}),
                         Path("/s.json"))


class FeedbackTests(unittest.TestCase):
    def test_feedback_is_local_atomic_and_never_inside_the_repository(self):
        self.assertIsNone(feedback_path(DEMO, environ={}))
        self.assertIsNone(feedback_path(None, environ={"PROSPECT_SHORTLIST_FEEDBACK": str(ROOT / "f.json")}))
        with tempfile.TemporaryDirectory() as directory:
            target = feedback_path(Path(directory) / "shortlist.json", environ={})
            self.assertEqual(target, Path(directory) / "shortlist-feedback.json")
            save_feedback(target, "r-1", "would_apply")
            save_feedback(target, "r-2", "maybe")
            save_feedback(target, "r-2", None)
            entries = load_feedback(target)
            self.assertEqual(list(entries), ["r-1"])
            self.assertEqual(entries["r-1"]["decision"], "would_apply")
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual([p.name for p in Path(directory).iterdir()], ["shortlist-feedback.json"])
            with self.assertRaises(ValueError):
                save_feedback(target, "r-1", "score")


@unittest.skipUnless(importlib.util.find_spec("streamlit"), "requires the optional dashboard dependencies")
class ShortlistAppTests(unittest.TestCase):
    def app(self, **environ):
        from streamlit.testing.v1 import AppTest

        environ.setdefault("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("PROSPECT_DATA_DIR", "PROSPECT_SHORTLIST", "PROSPECT_SHORTLIST_FEEDBACK")}
        # Keep the environment for every later rerun in the test.
        self.enterContext(patch.dict(os.environ, {**clean, **environ}, clear=True))
        return AppTest.from_file(str(ROOT / "dashboard" / "app.py"), default_timeout=15).run()

    def test_empty_state_without_any_shortlist(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.app(PROSPECT_DATA_DIR=directory)
        self.assertEqual(app.sidebar.radio[0].value, "Shortlist")
        self.assertTrue(any("No shortlist yet" in notice.value for notice in app.info))
        self.assertFalse(app.exception or app.error)
        self.assertFalse([r for r in app.radio if (r.key or "").startswith("feedback_")])

    def test_demo_cards_filters_and_session_only_feedback(self):
        app = self.app(PROSPECT_SHORTLIST=str(DEMO))
        self.assertFalse(app.exception)
        # Nothing sits between the "Shortlist" header and the search bar.
        main = list(app.main)
        header = next(i for i, e in enumerate(main) if getattr(e, "value", None) == "Shortlist")
        self.assertEqual(main[header + 1].text_input[0].key, "shortlist_search")
        self.assertEqual([e.value for e in app.error], ["No longer open (checked Nov 10, 2026) · Priority: Deprioritize"])
        self.assertTrue(any("DEMO SHORTLIST" in notice.value for notice in app.warning))
        self.assertIn("**Showing 24 of 24 curated jobs** · 16 verified current · Generated "
                      "Nov 15, 2026 at 00:00 UTC", [m.value for m in app.markdown])
        self.assertTrue(any("NOT YET VERIFIED" in notice.value for notice in app.warning))
        self.assertTrue(any("Verified current on Nov 1, 2026" in s.value for s in app.success))
        self.assertTrue(any("\\$58k" in c.value for c in app.caption))
        text = " ".join(m.value for m in app.markdown)
        self.assertNotIn("Why it fits", text)
        self.assertNotIn("Check before applying", text)
        self.assertIn("**Pulled from the listing**", text)
        self.assertIn("| Source job ID | demo-4100 |", text)
        self.assertIn("| Source snapshot | targeted\\_snapshot |", text)
        self.assertIn("| Provider | greenhouse |", text)
        self.assertIn("| Identity | native:greenhouse:demo-civic-lab:demo-4100 |", text)
        self.assertIn("| Employment type | _not captured_ |", text)
        self.assertIn("What we are looking for", text)
        self.assertTrue(any(e.label.startswith("Show full description") for e in app.expander))
        self.assertIn("**Source:** example\\.org · _from the posting link_", text)
        self.assertTrue(any("does not include Prospect's extracted listing fields" in c.value
                            for c in app.caption))
        self.assertTrue(any(c.value == "Description: not captured." for c in app.caption))
        self.assertTrue(any("session only" in c.value for c in app.caption))

        app.multiselect(key="shortlist_availability").set_value(["not_yet_verified"]).run()
        app.multiselect(key="shortlist_priority").set_value(["verify_first"]).run()
        self.assertIn("**Showing 4 of 24 curated jobs**", " ".join(m.value for m in app.markdown))
        app.radio(key="feedback_demo-003").set_value("would_apply").run()
        self.assertFalse(app.exception)
        app.text_input(key="shortlist_search").set_value("nothing matches this").run()
        self.assertTrue(any("No shortlisted jobs match" in notice.value for notice in app.info))

        # Other views remain; Browse Jobs still requires the discovery store.
        app.sidebar.radio[0].set_value("Browse Jobs").run()
        self.assertTrue(any("PROSPECT_DATA_DIR" in notice.value for notice in app.info))

    def test_invalid_file_is_reported_not_rendered(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shortlist.json"
            path.write_text(json.dumps({"schema_version": 9, "jobs": []}))
            app = self.app(PROSPECT_SHORTLIST=str(path))
        self.assertTrue(any("Unsupported schema_version" in error.value for error in app.error))

    def test_real_shortlist_feedback_saved_beside_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "shortlist.json"
            path.write_text(json.dumps({"schema_version": 1, "jobs": [record()]}))
            app = self.app(PROSPECT_DATA_DIR=directory)
            self.assertFalse(any("DEMO" in notice.value for notice in app.warning))
            app.radio(key="feedback_r-1").set_value("maybe").run()
            self.assertEqual(load_feedback(Path(directory) / "shortlist-feedback.json")["r-1"]["decision"],
                             "maybe")
