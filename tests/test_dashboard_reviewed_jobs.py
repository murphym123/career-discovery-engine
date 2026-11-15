"""Synthetic tests: private full-review data is browseable without hiding negatives."""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dashboard.reviewed_jobs import (
    ReviewResultsError, filter_reviews, load_review_results, review_results_path,
)

ROOT = Path(__file__).resolve().parents[1]


def job(review_id, category, **changes):
    data = {
        "review_id": review_id, "title": "Research Assistant",
        "employer": "brookings", "location": "Multiple Locations",
        "fit_category": category, "shortlisted": False,
        "needs_further_evidence": category == "possible",
        "posting_evidence": "Background research and drafting.",
        "applicant_evidence": "Relevant policy writing.",
        "barriers_or_unknowns": "Worksite and requirements need checking.",
        "official_url": "https://example.org/jobs/123",
        "availability_status": "not_yet_verified",
        "availability_checked_at": None,
    }
    data.update(changes)
    return data


class ReviewResultsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "deep_review.json"

    def write(self, records):
        self.path.write_text(json.dumps({"generated_at": "2026-11-15T07:00:00Z", "records": records}))
        return load_review_results(self.path)

    def test_all_categories_remain_visible_including_uncertain(self):
        result = self.write([job("a", "great_fit"), job("b", "possible"),
                             job("c", "poor_fit")])
        self.assertEqual(len(result["records"]), 3)
        self.assertEqual(result["records"][1]["employer"], "Brookings Institution")
        self.assertTrue(result["records"][1]["needs_further_evidence"])
        self.assertEqual([r["review_id"] for r in filter_reviews(result["records"], category="poor_fit")], ["c"])
        self.assertEqual(len(filter_reviews(result["records"], search="Brookings")), 3)

    def test_missing_file_and_env_resolution(self):
        self.assertIsNone(load_review_results(self.path))
        self.assertIsNone(review_results_path({}))
        self.assertEqual(review_results_path({"PROSPECT_SHORTLIST": "/somewhere/shortlist.json"}),
                         Path("/somewhere/deep_review.json"))
        self.assertEqual(review_results_path({"PROSPECT_REVIEW_RESULTS": "/other/review.json"}),
                         Path("/other/review.json"))

    def test_unsafe_links_redacted_and_no_false_verification(self):
        result = self.write([job("a", "possible", official_url="javascript:alert(1)",
                                 availability_status="verified_current",
                                 availability_checked_at=None)])
        row = result["records"][0]
        self.assertIsNone(row["official_url"])
        self.assertEqual(row["availability_status"], "unverified")

    def test_invalid_categories_and_duplicates_rejected(self):
        for records in ([job("a", "bad")], [job("a", "possible"), job("a", "poor_fit")]):
            with self.assertRaises(ReviewResultsError):
                self.write(records)


@unittest.skipUnless(importlib.util.find_spec("streamlit"), "requires Streamlit")
class ReviewedAppTests(unittest.TestCase):
    def test_view_shows_all_assessment_categories(self):
        from streamlit.testing.v1 import AppTest
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "deep_review.json"
            path.write_text(json.dumps({"records": [
                job("a", "great_fit"), job("b", "possible"), job("c", "poor_fit")
            ]}))
            env = {k: v for k, v in os.environ.items()
                   if k not in ("PROSPECT_SHORTLIST", "PROSPECT_DATA_DIR", "PROSPECT_REVIEW_RESULTS")}
            env["PROSPECT_REVIEW_RESULTS"] = str(path)
            with patch.dict(os.environ, env, clear=True):
                app = AppTest.from_file(str(ROOT / "dashboard/app.py"), default_timeout=18).run()
                app.sidebar.radio[0].set_value("Reviewed Jobs").run()
                self.assertFalse(app.exception)
                rendered = " ".join(x.value for x in app.markdown)
                self.assertIn("Showing 3 of 3 reviewed jobs", rendered)
                app.selectbox(key="reviewed_category").set_value("poor_fit").run()
                self.assertIn("Showing 1 of 3 reviewed jobs", " ".join(x.value for x in app.markdown))
                self.assertFalse(app.exception)
