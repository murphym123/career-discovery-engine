import csv
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from career_engine.__main__ import clean, main, score
from career_engine.discovery import deduplicate, expired, exploration_sample, geography, screening_hold
from career_engine.queries import FAMILIES, query_plan
from career_engine.sources import SourceStats, free_sources, normalize, retrieve
from tools.fixture_harness import FixtureSession, Response, TEST_ENV, load_fixture
from tools.benchmark_discovery import run_case
from career_engine import __main__ as engine


def prepared(**fields):
    job = clean(dict(title="Policy Associate", company="Example", location="Washington, DC",
                     url="https://example.org/job", source="muse", **fields))
    job["score"], job["lane"] = score(job)
    return job


class SourceTests(unittest.TestCase):
    def test_documented_shapes_all_adapters(self):
        for source in ("jobicy", "muse", "reliefweb", "usajobs"):
            with self.subTest(source=source), patch.dict(os.environ, TEST_ENV, clear=True):
                stats = SourceStats()
                rows = list(retrieve(FixtureSession(), source, 6, stats))
                self.assertGreater(len(rows), 0)
                self.assertFalse(stats.errors)
                self.assertTrue(all(clean(row) for row in rows))
                self.assertTrue(all(row.get("source_id") for row in rows))
                self.assertNotIn("<p>", rows[0].get("description", ""))

    def test_jobicy_cursor_keeps_filters_and_is_always_free(self):
        session, stats = FixtureSession(), SourceStats()
        with patch.dict(os.environ, {"JOBICY_API_KEY": "must-never-be-used"}, clear=True):
            rows = list(retrieve(session, "jobicy", 6, stats))
        self.assertEqual(stats.malformed, 2)
        self.assertEqual(stats.requests, 2)
        self.assertEqual(len(rows), 4)
        self.assertEqual(session.calls[1][1]["params"]["cursor"], "opaque page 2/+=")
        for _, call in session.calls:
            self.assertEqual(call["params"]["geo"], "usa")
            self.assertNotIn("Authorization", call["headers"])
            self.assertNotIn("tag", call["params"])
            self.assertNotIn("industry", call["params"])
            self.assertFalse(call["allow_redirects"])

    def test_muse_rotates_geography_without_title_filters(self):
        session = FixtureSession()
        with patch.dict(os.environ, TEST_ENV, clear=True):
            list(retrieve(session, "muse", 6))
        self.assertEqual(len({kw["params"]["location"] for _,kw in session.calls}), 6)
        for _, call in session.calls:
            self.assertFalse({"company", "level", "category"} & set(call["params"]))

    def test_optional_credentials_never_trigger_requests(self):
        for source in ("muse", "reliefweb", "usajobs"):
            session, stats = Mock(), SourceStats()
            with self.subTest(source=source), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(list(retrieve(session, source, stats=stats)), [])
            session.get.assert_not_called()
            self.assertEqual(stats.status, "not_configured")

    def test_free_selection_ignores_paid_keys(self):
        self.assertEqual(free_sources({"BRAVE_API_KEY":"present", "JOBICY_API_KEY":"present"}), ["remotive", "jobicy"])
        self.assertEqual(len(free_sources(TEST_ENV)), 5)

    def test_page_cap_and_no_retries(self):
        session, stats = FixtureSession(), SourceStats()
        list(retrieve(session, "jobicy", 1, stats))
        self.assertEqual(stats.requests, 1)
        self.assertTrue(stats.capped)
        for status in (429, 403, 500, 302):
            session = FixtureSession({"jobicy": [Response(status=status)]})
            stats = SourceStats()
            self.assertEqual(list(retrieve(session, "jobicy", 6, stats)), [])
            self.assertEqual(stats.requests, 1)
            self.assertTrue(stats.errors)

    def test_later_page_failure_preserves_records_and_redacts_details(self):
        fixture = load_fixture()
        fixture["jobicy"][1] = requests.ConnectionError("https://secret?api_key=PRIVATE")
        stats = SourceStats()
        rows = list(retrieve(FixtureSession(fixture), "jobicy", 6, stats))
        self.assertEqual(len(rows), 3)
        self.assertEqual(stats.status, "failed")
        self.assertNotIn("PRIVATE", str(stats.errors))

    def test_invalid_payloads_are_visible_failures(self):
        for payload in ({}, {"jobs":{}}, {"jobs":[],"success":False}, []):
            stats = SourceStats()
            self.assertEqual(list(retrieve(FixtureSession({"jobicy":[payload]}), "jobicy", stats=stats)), [])
            self.assertEqual(stats.status, "failed")

    def test_repeated_cursor_stops(self):
        fixture = load_fixture()
        fixture["jobicy"][1]["nextCursor"] = fixture["jobicy"][0]["nextCursor"]
        stats = SourceStats()
        list(retrieve(FixtureSession(fixture), "jobicy", 6, stats))
        self.assertEqual(stats.requests, 2)
        self.assertEqual(stats.status, "failed")

    def test_three_hundred_records_with_overlap(self):
        template=load_fixture()["jobicy"][0]["jobs"][0]
        pages=[]
        for page in range(3):
            rows=[]
            for index in range(100):
                identity=page*90+index
                rows.append(dict(template,id=identity, url=f"https://example.org/scale/{identity}"))
            pages.append({"jobs":rows,"nextCursor":str(page+1) if page<2 else None})
        stats=SourceStats()
        rows=list(retrieve(FixtureSession({"jobicy":pages}),"jobicy",3,stats))
        self.assertEqual(len(rows),300)
        self.assertEqual(len(deduplicate([clean(row) for row in rows])),280)
        self.assertEqual(stats.requests,3)
        self.assertFalse(stats.capped)

    def test_reliefweb_and_usajobs_paginate_without_keyword_gates(self):
        for source, count_key, rows_key in (("reliefweb","totalCount","data"), ("usajobs","SearchResultCountAll","SearchResultItems")):
            fixture = load_fixture()
            page = fixture[source][0]
            container = page if source == "reliefweb" else page["SearchResult"]
            container[count_key] = 101
            fixture[source].append(json.loads(json.dumps(page)))
            stats, session = SourceStats(), FixtureSession(fixture)
            with patch.dict(os.environ, TEST_ENV, clear=True):
                rows = list(retrieve(session, source, 6, stats))
            self.assertEqual(stats.requests, 2)
            self.assertEqual(len(rows), 6)
            self.assertNotIn("Keyword", session.calls[0][1]["params"])
            if source == "reliefweb":
                self.assertEqual(session.calls[1][1]["params"]["offset"], 100)
            else:
                self.assertEqual(session.calls[1][1]["params"]["Page"], 2)


class ScreeningTests(unittest.TestCase):
    def test_url_overlap_preserves_richness_and_provenance(self):
        a = prepared(source_id="101")
        b = dict(a, source="brave", source_id="", description="", sources="brave", key="other")
        rows = deduplicate([b,a])
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["source"],"muse")
        self.assertEqual(rows[0]["sources"],"brave,muse")

    def test_distinct_requisitions_same_title_survive(self):
        a = prepared(source_id="101")
        b = dict(a, source_id="102", url="https://example.org/other")
        b = clean(b)
        self.assertEqual(len(deduplicate([a,b])),2)

    def test_cross_board_url_with_tracking_and_missing_employer(self):
        a = prepared(source_id="101")
        b = clean(dict(a, company="", source="jobicy", source_id="201", url=a["url"]+"?utm_source=x"))
        self.assertEqual(len(deduplicate([a,b])),1)

    def test_identity_bridge_merges_groups(self):
        a = prepared(source_id="101")
        b = prepared(source_id="102")
        b["url"] = "https://example.org/other"
        bridge = dict(b, url=a["url"])
        self.assertEqual(len(deduplicate([a,b,bridge])),1)

    def test_bad_urls_and_empty_titles_rejected(self):
        for url in ("", "javascript:alert(1)", "not a URL"):
            self.assertIsNone(clean({"title":"Research", "url":url}))
        self.assertIsNone(clean({"title":"", "url":"https://example.org/"}))

    def test_dc_and_state_disambiguation(self):
        expected = {"Washington, DC":"dc_dmv", "Washington, District of Columbia":"dc_dmv",
                    "Seattle, Washington":"elsewhere", "Arlington, TX":"elsewhere",
                    "Arlington, VA":"dc_dmv", "Silver Spring, MD":"dc_dmv",
                    "Remote; USA":"us_remote", "Remote; Germany":"remote_eligibility_unknown",
                    "United States":"unknown", "":"unknown"}
        for value,label in expected.items():
            with self.subTest(location=value): self.assertEqual(geography(value),label)

    def test_expiry_uses_dates_not_absence_from_a_feed(self):
        now = datetime(2026,11,4,12,tzinfo=timezone.utc)
        self.assertTrue(expired({"closing_at":"2000-01-01"},now))
        self.assertFalse(expired({"closing_at":"2026-11-04"},now))
        self.assertFalse(expired({"closing_at":"nonsense"},now))
        self.assertFalse(expired({},now))

    def test_exploration_is_not_a_score_cutoff(self):
        jobs = [dict(url=f"https://example.org/{i}", score=0) for i in range(10)]
        first = exploration_sample(jobs,5)
        self.assertEqual(first, exploration_sample(list(reversed(jobs)),5))
        self.assertEqual(len(first),5)

    def test_specialist_and_senior_roles_are_reviewed_not_recommended(self):
        for title in ("Senior Cloud Security Analyst", "Sr. Manager, Success Operations", "Principal, Business Operations", "Director of Policy"):
            self.assertEqual(screening_hold({"title":title}),"seniority_review")
        for title in ("New Graduate Engineer", "Financial Analyst", "Commercial Counsel"):
            self.assertEqual(screening_hold({"title":title}),"specialist_background_review")
        for title in ("Democracy Fellow","Special Assistant, Strategic Initiatives","Government Affairs Coordinator"):
            self.assertEqual(screening_hold({"title":title}),"")


class QueryTests(unittest.TestCase):
    def test_budget_and_family_rotation(self):
        for offset in (0,6,10,37):
            plan = query_plan(10,offset)
            self.assertEqual({q.family for q in plan},set(FAMILIES))
            self.assertEqual(len(query_plan(6,offset)),6)
        self.assertEqual(len({q.family for q in query_plan(6)+query_plan(6,6)}),10)
        self.assertNotEqual(query_plan(10)[0].text,query_plan(10,10)[0].text)
        self.assertIn("Bethesda",query_plan(1,20)[0].text)
        self.assertIn("remote",query_plan(1,30)[0].text)
        self.assertNotIn("associate",query_plan(1,9)[0].text)


class IntegratedTests(unittest.TestCase):
    def run_engine(self, root, sources="free", extra=(), fixture=None, env=TEST_ENV):
        session = FixtureSession(fixture)
        argv = ["engine","--sources",sources,"--db",str(root/"jobs.db"),"--output",str(root/"brief.md"),*extra]
        with patch.dict(os.environ,env,clear=True), patch("sys.argv",argv), patch("career_engine.__main__.requests.Session",return_value=session), redirect_stdout(io.StringIO()):
            result = main()
        with (root/"screening_audit.csv").open(newline="",encoding="utf-8") as f:
            audit = list(csv.DictReader(f))
        return result, audit, json.loads((root/"discovery_metrics.json").read_text()), session

    def test_full_pipeline_and_history_offline(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            result,audit,metrics,session=self.run_engine(root)
            self.assertEqual(result,0)
            self.assertEqual(set(metrics["sources"]),set(("remotive","jobicy","muse","reliefweb","usajobs")))
            self.assertGreater(metrics["duplicates"],0)
            self.assertEqual(metrics["sources"]["jobicy"]["malformed"],2)
            self.assertIn("exploration_review",{j["decision"] for j in audit})
            self.assertEqual(next(j for j in audit if j["company"]=="Expired Example")["decision"],"expired_by_source_date")
            self.assertEqual(next(j for j in audit if j["company"]=="West Coast Lab" and j["source"]=="muse")["decision"],"geography_review")
            self.assertEqual(len([j for j in audit if j["title"]=="Research Associate" and j["company"]=="Capitol Evidence Lab"]),2)
            for title in ("Democracy Fellow","Partnerships Navigator"):
                self.assertIn(title,{j["title"] for j in audit})
            result2,_,metrics2,_=self.run_engine(root)
            self.assertEqual(result2,0)
            self.assertEqual(metrics["unique"],metrics2["unique"])
            self.assertIn("Previously seen",(root/"brief.md").read_text())
            self.assertTrue(all(source!="brave" for source,_ in session.calls))

    def test_default_no_credentials_and_no_paid_calls(self):
        with tempfile.TemporaryDirectory() as td:
            result,_,metrics,session=self.run_engine(Path(td),env={"BRAVE_API_KEY":"present"})
            self.assertEqual(result,0)
            self.assertEqual(set(metrics["sources"]),{"remotive","jobicy"})
            self.assertEqual(len(metrics["not_configured"]),3)

    def test_partial_source_failure_still_writes_audit(self):
        fixture=load_fixture();fixture["muse"][0]=Response(status=429)
        with tempfile.TemporaryDirectory() as td:
            result,audit,metrics,_=self.run_engine(Path(td),fixture=fixture)
            self.assertEqual(result,1)
            self.assertGreater(len(audit),0)
            self.assertEqual(metrics["sources"]["muse"]["status"],"rate_limited")

    def test_paid_search_blocked_before_session_created(self):
        with patch("sys.argv",["engine","--sources","brave"]), patch("career_engine.__main__.requests.Session") as session, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):main()
        session.assert_not_called()

    def test_search_rotation_persists_without_real_search_requests(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for expected in (0,6):
                with patch("career_engine.__main__.brave",return_value=[]), patch("time.sleep"):
                    _,_,metrics,_=self.run_engine(root,"brave",("--allow-paid-search",))
                self.assertEqual(metrics["queries"][0]["family"],query_plan(6,expected)[0].family)
                self.assertEqual(metrics["sources"]["brave"]["requests"],6)

    def test_measured_benchmark_cases_remain_reproducible(self):
        root=Path(__file__).resolve().parents[1]
        recorded=json.loads((root/"docs/benchmark.json").read_text())
        self.assertEqual(recorded["fixture_sha256"],hashlib.sha256((root/"tests/fixtures/discovery.json").read_bytes()).hexdigest())
        self.assertEqual(run_case(engine,"free"),recorded["cases"]["new_free_no_credentials"])
        self.assertEqual(run_case(engine,"free",env=TEST_ENV),recorded["cases"]["new_all_free_configured"])


if __name__ == "__main__":
    unittest.main()
