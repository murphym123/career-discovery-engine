"""Offline tests for the local viewer; no Streamlit or live network needed."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dashboard.queries import listings, open_store, overview, polls, runs, sources


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data_dir = Path(self.temporary.name)
        path = self.data_dir / "prospect.sqlite"
        schema = (Path(__file__).resolve().parents[1] /
                  "career_engine" / "store" / "schema.sql").read_text()
        with sqlite3.connect(path) as connection:
            connection.executescript(schema)
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("""
                INSERT INTO run
                  (run_id, started_at, finished_at, code_sha, config_sha, trigger, arm)
                VALUES ('r1', '2026-11-13T10:00:00Z', '2026-11-13T10:10:00Z',
                        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'hash',
                        'test', 'seeded')
            """)
            channels = [
                ("public", "public", "discovery"),
                ("restricted", "restricted", "discovery"),
                ("reference", "public", "reference"),
            ]
            for name, retention, role in channels:
                connection.execute("""
                    INSERT INTO channel
                      (channel_id, channel_key, kind, platform, locator,
                       retention_class, evidence_role, verification_state)
                    VALUES (?, ?, 'ats', ?, ?, ?, ?, 'verified')
                """, (name, name, name, "https://example.org/" + name, retention, role))
            poll_specs = [
                ("p1", "public", "ok", 2, "2026-11-13T10:01:00Z"),
                ("p2", "public", "partial", 1, "2026-11-13T10:02:00Z"),
                ("p3", "restricted", "ok", 1, "2026-11-13T10:03:00Z"),
                ("p4", "reference", "ok", 1, "2026-11-13T10:04:00Z"),
            ]
            for poll_id, channel_id, status, received, at in poll_specs:
                connection.execute("""
                    INSERT INTO poll
                      (poll_id, run_id, channel_id, started_at, status,
                       received, unrepresented)
                    VALUES (?, 'r1', ?, ?, ?, ?, 0)
                """, (poll_id, channel_id, at, status, received))
            for posting in ("job1", "secret", "reference-job"):
                connection.execute("""
                    INSERT INTO posting
                      (posting_id, identity_key, first_seen, last_seen)
                    VALUES (?, ?, '2026-11-13T10:01:00Z', '2026-11-13T10:04:00Z')
                """, (posting, "identity:" + posting))
            sightings = [
                ("s1", "job1", "p1", "public", "First title", "2026-11-13T10:01:00Z"),
                ("s2", "job1", "p2", "public", "Latest title", "2026-11-13T10:02:00Z"),
                ("s3", "secret", "p3", "restricted", "PRIVATE TITLE", "2026-11-13T10:03:00Z"),
                ("s4", "reference-job", "p4", "reference", "SEALED REFERENCE", "2026-11-13T10:04:00Z"),
            ]
            for sid, posting_id, poll_id, channel_id, title, at in sightings:
                connection.execute("""
                    INSERT INTO sighting
                      (sighting_id, posting_id, poll_id, channel_id, url,
                       canonical_url, title, employer_name_raw, location_raw, observed_at)
                    VALUES (?, ?, ?, ?, 'https://example.org/job',
                            'https://example.org/job', ?, 'Example Employer',
                            'Washington, DC', ?)
                """, (sid, posting_id, poll_id, channel_id, title, at))
            connection.commit()

    def test_public_counts_and_recent_runs(self):
        with open_store(self.data_dir) as connection:
            metrics = overview(connection)
            self.assertEqual(metrics["sources"], 1)
            self.assertEqual(metrics["polls"], 2)
            self.assertEqual(metrics["unique_jobs"], 1)
            self.assertEqual(metrics["incomplete_polls"], 1)
            self.assertEqual(len(runs(connection)), 1)
            self.assertEqual(runs(connection)[0]["rows_received"], 3)

    def test_public_sources_and_poll_results_only(self):
        with open_store(self.data_dir) as connection:
            found = sources(connection)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["platform"], "public")
            self.assertEqual(found[0]["latest_status"], "partial")
            self.assertEqual(found[0]["polls"], 2)
            self.assertEqual(len(polls(connection)), 2)
            self.assertNotIn("PRIVATE TITLE", str(polls(connection)))

    def test_latest_public_listing_excludes_private_and_reference(self):
        with open_store(self.data_dir) as connection:
            found = listings(connection)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]["title"], "Latest title")
            self.assertNotIn("PRIVATE TITLE", str(found))
            self.assertNotIn("SEALED REFERENCE", str(found))

    def test_read_only_connection(self):
        with open_store(self.data_dir) as connection:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute(
                    "INSERT INTO run (run_id, started_at, code_sha, config_sha, trigger, arm) "
                    "VALUES ('write', 'now', 'sha', 'sha', 'manual', 'test')"
                )

    def test_missing_store_is_not_created(self):
        missing = self.data_dir / "missing"
        with self.assertRaises(FileNotFoundError):
            with open_store(missing):
                pass
        self.assertFalse(missing.exists())

    def test_non_prospect_database_rejected(self):
        invalid = self.data_dir / "invalid"
        invalid.mkdir()
        sqlite3.connect(invalid / "prospect.sqlite").close()
        with self.assertRaises(ValueError):
            with open_store(invalid):
                pass


if __name__ == "__main__":
    unittest.main()
