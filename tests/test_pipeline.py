import csv
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from career_engine.__main__ import main


class OfflinePipelineTests(unittest.TestCase):
    def test_search_result_not_misrepresented_as_confirmed_job(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            candidates = root / "input.csv"
            with candidates.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["title", "company", "location", "url", "snippet", "source"])
                writer.writeheader()
                writer.writerow({
                    "title": "Special Assistant, Policy Research",
                    "company": "",
                    "location": "Washington, DC",
                    "url": "https://example.org/jobs/assistant",
                    "snippet": "Policy and research in Washington",
                    "source": "brave",
                })
                writer.writerow({
                    "title": "Research Associate",
                    "company": "Civic Research",
                    "location": "Washington, DC",
                    "url": "https://another-example.org/jobs/associate",
                    "snippet": "Political research writing",
                    "source": "remotive",
                })
            args = [
                "engine", "--sources", "csv", "--import-csv", str(candidates),
                "--min-score", "1", "--db", str(root / "jobs.db"),
                "--output", str(root / "brief.md"),
            ]
            with patch("sys.argv", args), redirect_stdout(io.StringIO()):
                result = main()
            self.assertEqual(result, 0)
            brief = (root / "brief.md").read_text()
            self.assertIn("Unverified web search leads", brief)
            self.assertIn("Special Assistant, Policy Research", brief)
            table = brief.split("## Unverified web search leads")[0]
            self.assertNotIn("Special Assistant, Policy Research", table)
            self.assertIn("Research Associate", table)
            audit = list(csv.DictReader((root / "screening_audit.csv").open(newline="", encoding="utf-8")))
            self.assertEqual(len(audit), 2)
            labels = {x["title"]: x["decision"] for x in audit}
            self.assertEqual(labels["Special Assistant, Policy Research"], "unverified_web_lead")
            self.assertEqual(labels["Research Associate"], "recommended")


if __name__ == "__main__":
    unittest.main()
