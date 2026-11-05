"""Regression against authentic minimized pages, plus synthetic failure cases."""
import ast
import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import requests
from career_engine.__main__ import clean, main
from career_engine.dc_sources import (Document, INDEXES, UNION_RSS,parse_index,parse_detail,
                                     parse_union_rss,retrieve_dc,listing_url,observe_selected_details)
from career_engine.sources import SourceStats

FIXTURES=Path(__file__).parent/"fixtures/real_dc"


def fixture(name):
    return (FIXTURES/name).read_text()


class Response:
    def __init__(self,text="",status=200,headers=None):
        self.text,self.status_code=text,status
        self.headers=headers or {"Content-Type":"text/html; charset=utf-8"}
        self.closed=False

    def iter_content(self,size):
        yield self.text.encode()

    def close(self):
        self.closed=True


class Session:
    def __init__(self, overrides=None):
        self.calls=[]
        self.pages={
            "https://careers.employment.senate.gov/robots.txt":Response(status=404),
            "https://www.unionjobs.com/robots.txt":Response(status=404),
            INDEXES["senate"][0]:Response(fixture("senate-index.html")),
            UNION_RSS:Response(fixture("union-feed.xml"),headers={"Content-Type":"application/rss+xml"}),
        }
        for url in INDEXES["unionjobs"]:
            self.pages[url]=Response(fixture("union-index.html"))
        manifest=json.loads(fixture("manifest.json"))
        for name in ("senate-detail.html","union-detail.html","epi-detail.html"):
            self.pages[manifest["files"][name]["url"]]=Response(fixture(name))
        self.pages.update(overrides or {})

    def get(self,url,**kwargs):
        self.calls.append(url)
        if url not in self.pages:
            raise AssertionError("Unexpected request "+url)
        value=self.pages[url]
        if isinstance(value,Exception):raise value
        return value


class AuthenticPageTests(unittest.TestCase):
    def test_manifest_hashes_and_authentic_provenance(self):
        manifest=json.loads(fixture("manifest.json"))
        for name,meta in manifest["files"].items():
            self.assertEqual(hashlib.sha256((FIXTURES/name).read_bytes()).hexdigest(),meta["fixture_sha256"])
            self.assertEqual(meta["http_status"],200)
            self.assertTrue(meta["url"].startswith("https://"))

    def test_real_senate_cards_keep_actual_offices_and_dc_location(self):
        rows=parse_index("senate",fixture("senate-index.html"),INDEXES["senate"][0])
        self.assertEqual(len(rows),3)
        staff=next(row for row in rows if row["source_id"]=="1283")
        self.assertEqual(staff["company"],"Senator John Fetterman")
        self.assertEqual(staff["location"],"Washington, District of Columbia")
        self.assertEqual(staff["title"],"Staff Assistant")
        self.assertEqual(staff["detail_status"],"not_requested")

    def test_real_union_cards_and_requirements(self):
        rows=parse_index("unionjobs",fixture("union-index.html"),INDEXES["unionjobs"][0])
        self.assertEqual(len(rows),3)
        admin=next(row for row in rows if row["source_id"]=="31407")
        self.assertIn("AFL-CIO",admin["company"])
        self.assertIn("Washington, DC",admin["raw_location"])
        fields,status=parse_detail("unionjobs",fixture("union-detail.html"))
        self.assertEqual(status,"public_description_observed")
        self.assertIn("Executive Office capacity",fields["description"])
        epi,_=parse_detail("unionjobs",fixture("epi-detail.html"))
        self.assertIn("minimum 1-2 years",epi["description"])

    def test_real_rss_is_geographic_discovery_not_remote_volume(self):
        rows=parse_union_rss(fixture("union-feed.xml"))
        self.assertEqual(len(rows),2)
        self.assertTrue(all(row["source_id"] for row in rows))
        self.assertTrue(all("Washington" in row["location"] for row in rows))
        self.assertTrue(all(row["url"].startswith("https://") for row in rows))
        self.assertNotIn("Software Developer",{row["title"] for row in rows})

    def test_actual_experience_preference_does_not_become_required_years(self):
        fields,status=parse_detail("senate",fixture("senate-detail.html"))
        self.assertIn("experience is preferred",fields["description"])
        self.assertNotIn("required",fields["description"])


class RetrievalTests(unittest.TestCase):
    def test_feed_denial_stops_the_source_without_falling_back(self):
        session=Session({UNION_RSS:Response(status=429)});stats=SourceStats()
        self.assertEqual(list(retrieve_dc(session,"unionjobs",20,stats,pause=lambda _:None)),[])
        self.assertEqual(session.calls,["https://www.unionjobs.com/robots.txt",UNION_RSS])
        self.assertEqual(stats.status,"failed")

    def test_manual_real_sample_references_observed_records_not_fixtures(self):
        root=Path(__file__).resolve().parents[1]/"docs/phase1"
        records=json.loads((root/"real-records.json").read_text())
        sample=json.loads((root/"review-sample.json").read_text())
        identities={row["source"]+":"+row["source_id"] for row in records["records"]}
        self.assertEqual(len(identities),81)
        self.assertEqual(len(sample["opportunities"]),15)
        self.assertTrue(all(row["identity"] in identities for row in sample["opportunities"]))
        self.assertTrue(all(row["source_url"].startswith("https://") and row["experience_summary"] for row in sample["opportunities"]))
        self.assertFalse(records["summary"]["ranker_changed"])
        self.assertEqual(records["summary"]["verified_active_employer_postings"],0)

    def test_union_regional_sweep_deduplicates_ids_and_keeps_origins(self):
        stats,session=SourceStats(),Session()
        rows=list(retrieve_dc(session,"unionjobs",0,stats,pause=lambda _:None))
        self.assertEqual(len(rows),4)
        self.assertEqual(stats.unique,4)
        self.assertEqual(stats.pages,4)
        self.assertEqual(stats.requests,5)
        self.assertTrue(stats.capped)
        self.assertTrue(all(url in session.calls for url in INDEXES["unionjobs"]))
        self.assertEqual(len({row["source_id"] for row in rows}),len(rows))
        self.assertIn("Maryland",next(row for row in rows if row["source_id"]=="31407")["discovery_urls"])
        self.assertTrue(all(not row.get("discovery_urls","").startswith(" ; ") for row in rows))

    def test_zero_detail_budget_never_requests_a_job(self):
        session=Session()
        rows=list(retrieve_dc(session,"senate",0,pause=lambda _:None))
        self.assertEqual(len(rows),3)
        self.assertFalse(any("/job/" in url for url in session.calls))

    def test_blocked_detail_does_not_delete_cards_or_retry(self):
        rows=parse_index("senate",fixture("senate-index.html"),INDEXES["senate"][0])
        first=rows[0]["url"]
        for code in (403,429):
            stats,session=SourceStats(),Session({first:Response(status=code)})
            actual=list(retrieve_dc(session,"senate",20,stats,pause=lambda _:None))
            self.assertEqual(len(actual),3)
            self.assertEqual(session.calls.count(first),1)
            self.assertEqual(sum("/job/" in u for u in session.calls),1)
            self.assertEqual(stats.status,"partial_failure")

    def test_robots_rules_and_unavailable_robots_fail_closed(self):
        url="https://careers.employment.senate.gov/robots.txt"
        for response in (Response("User-agent: *\nDisallow: /"),Response(status=503)):
            session=Session({url:response});stats=SourceStats()
            rows=list(retrieve_dc(session,"senate",0,stats,pause=lambda _:None))
            self.assertEqual(rows,[])
            self.assertEqual(session.calls,[url])
            self.assertEqual(stats.status,"failed")

    def test_no_private_external_search_or_apply_urls_followed(self):
        for url in ("https://127.0.0.1/x","https://example.org/job/1",
                    "https://careers.employment.senate.gov/search/", "https://careers.employment.senate.gov/apply-for-job/123"):
            self.assertFalse(listing_url("senate",url))
        first=parse_index("senate",fixture("senate-index.html"),INDEXES["senate"][0])[0]["url"]
        session=Session({first:Response(status=302,headers={"Location":"https://127.0.0.1/"})})
        rows=list(retrieve_dc(session,"senate",1,pause=lambda _:None))
        self.assertEqual(rows[0]["detail_status"],"unsafe_url")
        self.assertFalse(any("127.0.0.1" in url for url in session.calls))

    def test_changed_detail_or_login_is_not_confirmed(self):
        for html in ("<html>Login required</html>","<html>Layout changed</html>"):
            fields,status=parse_detail("senate",html)
            self.assertEqual(fields,{})
            self.assertEqual(status,"detail_schema_changed")
        _,status=parse_detail("senate",'<div class="job-description">This position has been filled.</div>')
        self.assertEqual(status,"closed_notice_observed")

    def test_selected_sample_uses_only_observed_ids(self):
        rows=parse_index("senate",fixture("senate-index.html"),INDEXES["senate"][0])
        stats=observe_selected_details(Session(),rows,["senate:1283"],1,pause=lambda _:None)
        self.assertEqual(stats.requests,2)
        self.assertIn("preferred",next(r for r in rows if r["source_id"]=="1283")["description"])
        with self.assertRaises(ValueError):observe_selected_details(Session(),rows,["senate:unknown"],1)

    def test_oversized_page_and_network_failure_are_reported(self):
        for response,status in ((Response("x"*1_000_001),"page_too_large"),(requests.ConnectionError(),"network_error")):
            stats=SourceStats()
            self.assertEqual(list(retrieve_dc(Session({INDEXES["senate"][0]:response}),"senate",0,stats,pause=lambda _:None)),[])
            self.assertIn(status,str(stats.errors))

    def test_default_cli_selects_dc_and_retains_evidence_without_changing_scores(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            argv=["engine","--dc-detail-budget","0","--db",str(root/"jobs.db"),"--output",str(root/"brief.md")]
            with patch("sys.argv",argv),patch("requests.Session",return_value=Session()),patch("career_engine.dc_sources.time.sleep"),redirect_stdout(io.StringIO()):
                # Explicitly patch default-bound pause as well.
                with patch("career_engine.__main__.retrieve_dc",side_effect=lambda s,source,budget,stats:retrieve_dc(s,source,budget,stats,pause=lambda _:None)):
                    result=main()
            self.assertEqual(result,0)
            metrics=json.loads((root/"discovery_metrics.json").read_text())
            self.assertEqual(set(metrics["sources"]),{"senate","unionjobs"})
            with (root/"screening_audit.csv").open() as f:audit=list(csv.DictReader(f))
            self.assertTrue(all(row["evidence_status"] for row in audit))
            self.assertTrue(all(row["index_url"] for row in audit))


if __name__=="__main__":unittest.main()
