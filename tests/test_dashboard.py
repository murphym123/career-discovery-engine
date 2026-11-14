"""Offline tests for the local viewer using synthetic Prospect stores."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from career_engine.store import Store
from dashboard.queries import listings, open_store, overview, polls, runs, sources

START = "2026-11-13T10:00:00Z"
WHOLE = "2026-11-13T10:01:00Z"
FRACTION = "2026-11-13T10:01:00.000001Z"
LATER = "2026-11-13T10:02:00Z"


def channel(store, name, *, retention="public", role="discovery", locator=None):
    return store.ensure_channel(
        channel_id=name, kind="ats", platform=name,
        locator=locator or f"https://example.org/{name}",
        retention_class=retention, evidence_role=role, verification_state="verified",
    )


def run(store, name, at=START, *, trigger="test", arm="seeded"):
    return store.begin_run(
        run_id=name, code_sha="a" * 40, config_sha="b" * 64,
        trigger=trigger, arm=arm, started_at=at,
    )


def sighting(store, poll, title, at, *, identity="shared", url="https://example.org/job"):
    return store.record_sighting(
        poll, identity_key=identity, url=url, canonical_url=url,
        title=title, employer_name_raw="Example Employer",
        location_raw="Washington, DC", observed_at=at,
    )


class DashboardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data_dir = Path(temporary.name)
        self.path = self.data_dir / "prospect.sqlite"
        with Store(self.path):
            pass

    def populate(self):
        with Store(self.path) as store:
            public = channel(store, "greenhouse")
            channel(store, "jazzhr")
            email = channel(store, "newsletter", retention="private_email")
            reference = channel(store, "reference", role="reference")
            restricted = channel(store, "restricted", retention="restricted")
            old = run(store, "old", WHOLE)
            newer = run(store, "newer", FRACTION)
            private = run(store, "private", LATER, arm="PRIVATE_ONLY_RUN")
            run(store, "no-recorded-polls", LATER)

            first = store.record_poll(old, public, status="ok", received=1, started_at=WHOLE)
            partial = store.record_poll(
                newer, public, status="partial", received=2, unrepresented=1,
                declared_total=5, started_at=FRACTION, poll_id="c",
            )
            store.record_poll(
                newer, public, status="failed", received=0, started_at=FRACTION, poll_id="a",
            )
            store.record_poll(
                newer, public, status="skipped:budget", received=0, started_at=FRACTION, poll_id="b",
            )
            private_poll = store.record_poll(private, email, status="ok", received=2, started_at=LATER)
            reference_poll = store.record_poll(private, reference, status="ok", received=1, started_at=LATER)
            store.record_poll(private, restricted, status="skipped:restricted", received=0, started_at=LATER)
            sighting(store, first, "First title", WHOLE)
            sighting(store, partial, "Latest public title", FRACTION)
            sighting(store, private_poll, "PRIVATE TITLE", LATER)
            sighting(store, private_poll, "PRIVATE ONLY", LATER, identity="private-only")
            sighting(store, reference_poll, "SYNTHETIC REFERENCE", LATER, identity="reference-only")
            store.end_run(old, finished_at=LATER)
            store.end_run(private, finished_at=LATER)

    def test_public_counts_and_incomplete_runs(self):
        self.populate()
        with open_store(self.data_dir) as connection:
            self.assertEqual(overview(connection), dict(
                sources=2, polls=4, unique_jobs=1, incomplete_polls=3, last_poll=FRACTION,
            ))
            recent = runs(connection)
        self.assertEqual([row["started_at"] for row in recent], [FRACTION, WHOLE])
        self.assertIsNone(recent[0]["finished_at"])
        self.assertEqual(recent[0]["incomplete_polls"], 3)
        self.assertEqual(recent[0]["rows_received"], 2)
        self.assertEqual(recent[0]["unrepresented"], 1)

    def test_public_sources_and_poll_results(self):
        self.populate()
        with open_store(self.data_dir) as connection:
            observed, unpolled = sources(connection)
            results = polls(connection)
        self.assertEqual(observed["platform"], "greenhouse")
        self.assertEqual(observed["latest_status"], "partial")
        self.assertEqual(observed["last_polled"], FRACTION)
        self.assertEqual(observed["polls"], 4)
        self.assertEqual(observed["incomplete_polls"], 3)
        self.assertEqual(observed["rows_received"], 3)
        self.assertEqual(observed["unrepresented"], 1)
        self.assertEqual(unpolled["platform"], "jazzhr")
        self.assertEqual(unpolled["polls"], 0)
        self.assertEqual(unpolled["incomplete_polls"], 0)
        self.assertIsNone(unpolled["latest_status"])
        self.assertIsNone(unpolled["last_polled"])
        self.assertEqual([row["status"] for row in results], [
            "partial", "skipped:budget", "failed", "ok",
        ])
        self.assertEqual(results[0]["declared_total"], 5)
        self.assertEqual({row["platform"] for row in results}, {"greenhouse"})

    def test_latest_public_listing_excludes_newer_private_sightings(self):
        self.populate()
        with open_store(self.data_dir) as connection:
            found = listings(connection)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["title"], "Latest public title")
        self.assertEqual(found[0]["observed_at"], FRACTION)
        self.assertEqual(found[0]["url"], "https://example.org/job")

    def test_identical_metadata_with_distinct_posting_ids_stays_distinct(self):
        with Store(self.path) as store:
            poll = store.record_poll(
                run(store, "test"), channel(store, "lever"),
                status="ok", received=2, started_at=WHOLE,
            )
            sighting(store, poll, "Same title", WHOLE, identity="board:1")
            sighting(store, poll, "Same title", WHOLE, identity="board:2")
        with open_store(self.data_dir) as connection:
            self.assertEqual(overview(connection)["unique_jobs"], 2)
            self.assertEqual(len(listings(connection)), 2)
            self.assertEqual(len(listings(connection, limit=1)), 1)

    def test_sensitive_fields_on_public_channels_are_redacted(self):
        with Store(self.path) as store:
            batch = run(store, "test", trigger="password=example", arm="fixture-private-value")
            public = channel(store, "lever", locator="https://example.org/board?token=abc")
            poll = store.record_poll(batch, public, status="ok", received=3, started_at=WHOLE)
            sighting(store, poll, "Contact private@example.org", WHOLE,
                     url="https://example.org/job?signature=abc")
            sighting(store, poll, "Unsafe link", WHOLE, identity="unsafe", url="file:///tmp/private")
            sighting(store, poll, "Public role", WHOLE, identity="safe",
                     url="https://example.org/job?utm_source=abc&gh_jid=42#tracking")
        with patch.dict("os.environ", {"SYNTHETIC_API_KEY": "fixture-private-value"}):
            with open_store(self.data_dir) as connection:
                self.assertIsNone(sources(connection)[0]["locator"])
                self.assertIsNone(polls(connection)[0]["locator"])
                self.assertEqual(polls(connection)[0]["arm"], "[redacted]")
                self.assertEqual(runs(connection)[0]["trigger"], "[redacted]")
                found = {row["title"]: row for row in listings(connection)}
        self.assertIsNone(found["[redacted]"]["url"])
        self.assertIsNone(found["Unsafe link"]["url"])
        self.assertEqual(found["Public role"]["url"], "https://example.org/job?gh_jid=42")
        self.assertNotIn("canonical_url", found["Public role"])
        self.assertNotIn("posting_id", found["Public role"])

    def test_read_only_connection_even_with_query_only_disabled(self):
        before = self.path.read_bytes()
        with open_store(self.data_dir) as connection:
            self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
            for query_only in (True, False):
                if not query_only:
                    connection.execute("PRAGMA query_only=OFF")
                with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
                    connection.execute("DELETE FROM schema_version")
        self.assertEqual(self.path.read_bytes(), before)

    def test_wal_reads_commits_and_keeps_a_consistent_snapshot(self):
        uri_dir = self.data_dir / "path with ? and #"
        uri_dir.mkdir()
        with Store(uri_dir / "prospect.sqlite") as writer:
            channel(writer, "lever")
            with open_store(uri_dir) as reader:
                self.assertEqual(overview(reader)["sources"], 1)
                channel(writer, "greenhouse")
                self.assertEqual(len(sources(reader)), 1)
            with open_store(uri_dir) as reader:
                self.assertEqual(len(sources(reader)), 2)

    def test_empty_v1_store_without_newer_accounting_tables(self):
        with closing(sqlite3.connect(self.path)) as writer:
            writer.executescript("DROP TABLE poll_inventory; DROP TABLE poll_accounting;")
        with open_store(self.data_dir) as connection:
            self.assertEqual(overview(connection), dict(
                sources=0, polls=0, unique_jobs=0, incomplete_polls=0, last_poll=None,
            ))
            for query in (runs, sources, polls, listings):
                self.assertEqual(query(connection), [])
            self.assertIsNone(connection.execute(
                "SELECT name FROM sqlite_master WHERE name = 'poll_accounting'"
            ).fetchone())

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
        with self.assertRaisesRegex(ValueError, "not a supported Prospect"):
            with open_store(invalid):
                pass

    def test_unsupported_schema_version_rejected_without_migrating(self):
        with closing(sqlite3.connect(self.path)) as writer, writer:
            writer.execute("UPDATE schema_version SET version = 999")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "Unsupported Prospect store schema version"):
            with open_store(self.data_dir):
                pass
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
