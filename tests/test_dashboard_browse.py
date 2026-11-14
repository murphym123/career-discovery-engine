"""Search and browse synthetic observations without loading collector evidence."""
import importlib.util
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from career_engine.store import Store
from dashboard.queries import browse_jobs, job_sources, open_store
from test_dashboard import LATER, WHOLE, channel, run

ROOT = Path(__file__).resolve().parents[1]


class BrowseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.path = self.data / "prospect.sqlite"
        with Store(self.path) as store:
            self.batch = run(store, "jobs")
            self.lever = self.poll(store, "lever", received=30)
            self.greenhouse = self.poll(store, "greenhouse", received=3)

    def poll(self, store, platform, received, **channel_options):
        return store.record_poll(
            self.batch, channel(store, platform, **channel_options),
            status="ok", received=received, started_at=WHOLE,
        )

    def job(self, store, title, *, identity=None, poll=None, employer="Civic Institute",
            location="Washington, DC", at=WHOLE, url="https://example.org/jobs/civic",
            canonical_url=None, **dates):
        return store.record_sighting(
            poll or self.lever, identity_key=identity or title, title=title,
            employer_name_raw=employer, location_raw=location, observed_at=at,
            url=url, canonical_url=canonical_url or url, **dates,
        )

    def search(self, **filters):
        with open_store(self.data) as reader:
            return browse_jobs(reader, **filters)

    def test_combined_filters_unicode_literal_queries_and_sort(self):
        with Store(self.path) as store:
            self.job(store, "Research Associate", employer="École Civic", location="Denver, CO")
            self.job(store, "Research Assistant", employer="École Civic", location="Denver, CO")
            self.job(store, "Research Fellow", employer="École Civic", location="Unknown")
            self.job(store, "Research Analyst", employer="École Civic", location="Denver, CO",
                     poll=self.greenhouse)
            self.job(store, "Role 100%_remote", employer="Aid Lab", location="Outside DC", at=LATER)
        total, page, jobs = self.search(
            search="RESEARCH école", employer="ÉCOLE", location="denver", source="lever", sort="title",
        )
        self.assertEqual((total, page), (2, 1))
        self.assertEqual([job["title"] for job in jobs], ["Research Assistant", "Research Associate"])
        self.assertEqual(self.search(sort="employer")[2][0]["employer"], "Aid Lab")
        self.assertEqual(self.search()[2][0]["title"], "Role 100%_remote")
        self.assertEqual(self.search(location="outside dc")[0], 1)
        self.assertEqual(self.search(location="unknown")[0], 1)
        self.assertEqual(self.search(search="100%_remote")[0], 1)
        self.assertEqual(self.search(search="' OR 1=1 --"), (0, 1, []))

    def test_older_than_two_thousand_and_stable_page_boundaries(self):
        with Store(self.path) as store, store.transaction() as db:
            self.job(store, "Rare archival assistant")
            newer = self.job(store, "Repeated role", at=LATER)
            # The 2,005 newer rows share their display fields but have distinct IDs.
            db.executemany("INSERT INTO posting(posting_id, identity_key, first_seen, last_seen) "
                           "VALUES (?, ?, ?, ?)",
                           ((f"job-{i:04}", f"key-{i}", LATER, LATER) for i in range(2004)))
            db.executemany("""
                INSERT INTO sighting (
                    sighting_id, posting_id, poll_id, channel_id, url, canonical_url,
                    title, employer_name_raw, location_raw, observed_at
                )
                SELECT ?, ?, poll_id, channel_id, ?, '', title, employer_name_raw, location_raw, observed_at
                  FROM sighting WHERE posting_id = ?
            """, ((f"s-{i}", f"job-{i:04}", f"https://example.org/job/{i}", newer) for i in range(2004)))
        self.assertEqual(self.search(search="archival")[2][0]["title"], "Rare archival assistant")
        count, page, first = self.search()
        self.assertEqual((count, page, len(first)), (2006, 1, 12))
        second = self.search(page=2)[2]
        self.assertTrue({job["url"] for job in first}.isdisjoint(job["url"] for job in second))
        self.assertEqual(self.search(page=2)[2], second)
        count, page, last = self.search(page=999)
        self.assertEqual((count, page, len(last)), (2006, 168, 2))
        self.assertEqual(last[-1]["title"], "Rare archival assistant")

    def test_privacy_applies_before_matching_sorting_or_facets(self):
        with Store(self.path) as store:
            sensitive = self.poll(store, "fixture-secret", received=2)
            self.job(store, "password=TOP_SECRET", employer="Contact hr@private.example",
                     location="fixture-secret", poll=sensitive,
                     url="https://example.org/job?token=private")
            self.job(store, "Synthetic role", identity="safe", employer="", location="",
                     poll=sensitive, url="https://example.org/job?sig=private",
                     canonical_url="https://job-boards.greenhouse.io/civic/jobs/42", posted_at=WHOLE)
            self.job(store, "Synthetic role", identity="other", employer="", location="",
                     url="https://example.org/job?tracking=hidden&gh_jid=42")
            restricted = self.poll(store, "restricted-platform", received=1, role="reference")
            self.job(store, "Reference marker", poll=restricted, at=LATER, identity="safe")
            self.job(store, "Reference only", poll=restricted, at=LATER)
        with patch.dict(os.environ, {"BROWSE_API_KEY": "fixture-secret"}):
            with open_store(self.data) as reader:
                self.assertEqual(job_sources(reader), ["lever"])
                with self.assertRaises(ValueError):
                    browse_jobs(reader, source="fixture-secret")
            for filters in (
                {"search": "TOP_SECRET"}, {"employer": "private"}, {"location": "fixture"},
                {"search": "reference"}, {"source": "restricted-platform"}, {"search": "redacted"},
            ):
                self.assertEqual(self.search(**filters), (0, 1, []))
            total, _, jobs = self.search(sort="employer")
        self.assertEqual(total, 3)
        self.assertNotRegex(str(jobs), "TOP_SECRET|private|fixture-secret|Reference|tracking")
        self.assertEqual({job["url"] for job in jobs}, {
            None, "https://job-boards.greenhouse.io/civic/jobs/42", "https://example.org/job?gh_jid=42",
        })
        self.assertTrue(all(job["location"] is None for job in jobs))
        self.assertTrue(all("posting_id" not in job and "canonical_url" not in job for job in jobs))

    def test_wal_snapshot_and_no_write_access(self):
        with Store(self.path) as writer, open_store(self.data) as reader:
            self.assertEqual(browse_jobs(reader), (0, 1, []))
            self.job(writer, "WAL role")
            self.assertEqual(browse_jobs(reader), (0, 1, []))
            self.assertEqual(reader.execute("PRAGMA query_only").fetchone()[0], 1)
            reader.execute("PRAGMA query_only=OFF")
            with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
                reader.execute("DELETE FROM sighting")
        self.assertEqual(self.search(search="WAL")[0], 1)

    @unittest.skipUnless(importlib.util.find_spec("streamlit"), "requires the optional dashboard dependencies")
    def test_streamlit_browse_interactions_and_monitoring_views(self):
        from streamlit.testing.v1 import AppTest

        with Store(self.path) as store, store.transaction():
            for index in range(12):
                self.job(store, f"Analyst {index:02}")
            self.job(store, "Outreach Associate", employer="Transit Council",
                     location="Baltimore, MD", poll=self.greenhouse, posted_at=WHOLE)
            self.job(store, "  ", employer="", location="", url="file:///tmp/private")
        with patch.dict(os.environ, {
            "PROSPECT_DATA_DIR": str(self.data), "PROSPECT_DEMO": "1",
            "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
        }):
            app = AppTest.from_file(str(ROOT / "dashboard" / "app.py"), default_timeout=15).run()
            self.assertEqual(app.sidebar.radio[0].value, "Browse Jobs")
            self.assertTrue(app.sidebar.checkbox[0].disabled)
            self.assertIn("DEMO / SYNTHETIC DATA", [notice.value for notice in app.warning])
            self.assertIn("**14 matching posting IDs** · Showing 1–10", [m.value for m in app.markdown])

            app.button(key="next_top").click().run()
            self.assertTrue(app.button(key="next_top").disabled)
            self.assertIn("**14 matching posting IDs** · Showing 11–14", [m.value for m in app.markdown])
            self.assertIn("Job title unavailable", " ".join(m.value for m in app.markdown))
            self.assertIn("Original posting link unavailable", [caption.value for caption in app.caption])
            for view in ("Overview", "Run explorer", "Sources", "Live activity", "Browse Jobs"):
                app.sidebar.radio[0].set_value(view).run()
                self.assertFalse(app.exception or app.error, view)
            self.assertIn("**14 matching posting IDs** · Showing 11–14", [m.value for m in app.markdown])
            app.text_input(key="job_search").set_value("outreach baltimore").run()
            app.text_input(key="job_employer").set_value("Transit").run()
            app.text_input(key="job_location").set_value("Maryland").run()
            self.assertTrue(any("No postings match" in notice.value for notice in app.info))
            self.assertTrue(any("0 matching posting IDs" in m.value for m in app.markdown))
            app.text_input(key="job_location").set_value("Baltimore").run()
            app.selectbox(key="job_source").set_value("greenhouse").run()
            app.selectbox(key="job_sort").set_value("title").run()
            self.assertFalse(app.exception or app.error)
            self.assertIn("**1 matching posting ID** · Showing 1–1", [m.value for m in app.markdown])
            self.assertTrue(any("View original posting" in m.value for m in app.markdown))
            self.assertTrue(any("Posted Nov 13, 2026" in caption.value for caption in app.caption))
            next(button for button in app.button if button.label == "Reset filters").click().run()
            self.assertEqual(app.text_input(key="job_search").value, "")
            self.assertEqual(app.selectbox(key="job_source").value, None)
            self.assertIn("**14 matching posting IDs** · Showing 1–10", [m.value for m in app.markdown])
