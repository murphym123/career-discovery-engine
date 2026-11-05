import copy
import csv
import hashlib
import json
from pathlib import Path
import unittest
import io
import tempfile
from contextlib import redirect_stdout
from unittest.mock import patch

from career_engine.dc_sources import INDEXES,GLOBAL_RSS,listing_url,parse_index,parse_detail,retrieve_dc
from career_engine.discovery import geography
from career_engine.inspection import plan_inspections,early_signal,evidence_tier
from career_engine.sources import SourceStats
from career_engine.specialty_sources import parse_global_feed
from tools.audit_dc_sources import capture
from tools.compare_inspection import compare
from career_engine.__main__ import main

ROOT=Path(__file__).parent/"fixtures/real_specialty"


def fixture(name):return (ROOT/name).read_text()


class Response:
    def __init__(self,text="",status=200,mime="text/html",location=None):
        self.text,self.status_code=text,status;self.headers={"Content-Type":mime}
        if location:self.headers["Location"]=location
    def iter_content(self,size):yield self.text.encode()
    def close(self):pass


class Session:
    def __init__(self,overrides=None):
        self.calls=[];self.pages={GLOBAL_RSS:Response(fixture("global-feed.xml"),mime="application/rss+xml")}
        for source in ("globaljobs","onthinktanks","workfornonprofits"):
            self.pages[INDEXES[source][0]]=Response(fixture(source+"-index.html"))
        self.pages.update(overrides or {})
    def get(self,url,**kwargs):
        self.calls.append(url)
        if url.endswith("/robots.txt"):return Response(status=404)
        if url not in self.pages:raise AssertionError("Unexpected request "+url)
        return self.pages[url]


class AuthenticAdaptersTests(unittest.TestCase):
    def test_capture_integrates_all_three_new_sources_without_remote_fallback(self):
        with patch("tools.audit_dc_sources.retrieve_dc",side_effect=lambda s,source,budget,stats,**kwargs:retrieve_dc(s,source,budget,stats,pause=lambda _:None,**kwargs)):
            result=capture(("globaljobs","onthinktanks","workfornonprofits"),0,Session())
        self.assertEqual(len(result["records"]),13)
        self.assertEqual(set(result["stats"]),{"globaljobs","onthinktanks","workfornonprofits"})
        self.assertTrue(all(row["detail_status"]=="not_requested" for row in result["records"]))
    def test_actual_fixture_manifest_hashes(self):
        for name,meta in json.loads(fixture("manifest.json"))["files"].items():
            self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(),meta["fixture_sha256"])
            self.assertEqual(meta["status"],200)

    def test_global_visible_metadata_and_noncontiguous_real_records(self):
        rows=parse_index("globaljobs",fixture("globaljobs-index.html"),INDEXES["globaljobs"][0])
        self.assertEqual(len(rows),5)
        byid={row["source_id"]:row for row in rows}
        self.assertEqual(byid["52105"]["company"],"Carnegie Endowment for International Peace")
        self.assertIn("provider_early_career",byid["52105"]["experience_hint"])
        self.assertEqual(geography(byid["52105"]["location"]),"dc_dmv")
        self.assertEqual(geography(byid["52217"]["location"]),"elsewhere")

    def test_global_advertised_feed_and_index_merge_preserve_absolute_dates(self):
        stats=SourceStats();session=Session()
        rows=list(retrieve_dc(session,"globaljobs",0,stats,pause=lambda _:None))
        self.assertEqual(len(rows),5)
        self.assertEqual(stats.requests,3)
        self.assertEqual(stats.pages,2)
        self.assertGreater(stats.received,stats.unique)
        carnegie=next(row for row in rows if row["source_id"]=="52105")
        self.assertTrue(carnegie["posted_at"])
        self.assertNotEqual(carnegie["posted_at"],carnegie["posted_at_raw"])
        self.assertIn(GLOBAL_RSS,carnegie["discovery_urls"])
        self.assertTrue(all(row["detail_status"]=="not_requested" for row in rows))

    def test_think_tank_real_cards_and_explicit_deadline(self):
        rows=parse_index("onthinktanks",fixture("onthinktanks-index.html"),INDEXES["onthinktanks"][0])
        analyst=next(row for row in rows if row["source_id"]=="energy-policy-analyst")
        self.assertEqual(analyst["company"],"Cato Institute")
        self.assertEqual(analyst["closing_at"],"2027-07-01")
        fields,status=parse_detail("onthinktanks",fixture("ott-detail.html"))
        self.assertEqual(status,"public_description_observed")
        self.assertIn("7–10 years",fields["description"])

    def test_related_content_does_not_pollute_the_description(self):
        html='<main class="c-single--job"><div class="o-content-from-editor">Actual requirements</div><aside>Unrelated clinical vacancy</aside></main>'
        fields,status=parse_detail("onthinktanks",html)
        self.assertEqual(fields["description"],"Actual requirements")

    def test_nonprofit_real_public_cards_and_jobposting(self):
        rows=parse_index("workfornonprofits",fixture("workfornonprofits-index.html"),INDEXES["workfornonprofits"][0])
        street=rows[0]
        self.assertEqual(street["company"],"J Street")
        self.assertEqual(street["category"],"Development")
        self.assertEqual(street["employment_type"],"Full Time")
        self.assertTrue(street["posted_at_raw"])
        fields,status=parse_detail("workfornonprofits",fixture("nonprofit-detail.html"))
        self.assertEqual(status,"public_description_observed")
        self.assertIn("driver’s license",fields["description"])

    def test_actual_early_level_and_numeric_requirement_remain_distinct(self):
        fields,_=parse_detail("globaljobs",fixture("global-detail.html"))
        self.assertIn("entry-level",fields["description"])
        fields,_=parse_detail("globaljobs",fixture("global-wyss-detail.html"))
        self.assertIn("One to two years",fields["description"])

    def test_specialty_paths_cannot_call_private_apis_or_apply(self):
        for source in ("globaljobs","onthinktanks","workfornonprofits"):
            host=INDEXES[source][0].split('/')[2]
            for path in ("/api/jobs","/auth","/apply-for-job/1","/graphql"):
                self.assertFalse(listing_url(source,"https://"+host+path))
            self.assertFalse(listing_url(source,"https://127.0.0.1/job/1"))

    def test_new_source_failure_never_falls_back_to_paid_or_remote_feed(self):
        session=Session({GLOBAL_RSS:Response(status=429)});stats=SourceStats()
        self.assertEqual(list(retrieve_dc(session,"globaljobs",20,stats,pause=lambda _:None)),[])
        self.assertEqual(stats.status,"failed")
        self.assertEqual(len(session.calls),2)

    def test_changed_markup_reports_failure_and_closed_notice_not_open(self):
        session=Session({INDEXES["onthinktanks"][0]:Response("<main>New layout</main>")});stats=SourceStats()
        self.assertEqual(list(retrieve_dc(session,"onthinktanks",0,stats,pause=lambda _:None)),[])
        self.assertIn("No listing cards",str(stats.errors))
        fields,status=parse_detail("globaljobs",'<div class="prose-job-description">This position has been filled.</div>')
        self.assertEqual(status,"closed_notice_observed")

    def test_feed_rejects_xml_declarations(self):
        with self.assertRaises(ValueError):parse_global_feed('<!DOCTYPE rss><rss/>')


class InspectionTests(unittest.TestCase):
    def setUp(self):
        self.rows=parse_index("globaljobs",fixture("globaljobs-index.html"),INDEXES["globaljobs"][0])

    def test_caps_uniqueness_and_no_mutation_or_deletion(self):
        original=copy.deepcopy(self.rows)
        for cap in (0,1,2,3,20,30):
            plan=plan_inspections(self.rows,cap,5)
            self.assertLessEqual(len(plan),cap)
            self.assertEqual(len({x["identity"] for x in plan}),len(plan))
        self.assertEqual(self.rows,original)
        self.assertEqual(len(self.rows),5)

    def test_known_foreign_first_row_does_not_consume_regional_sentinel(self):
        plan=plan_inspections(self.rows,1,5)
        self.assertNotEqual(plan[0]["identity"],"globaljobs:52217")
        self.assertEqual(plan[0]["stratum"],"latest_regional_listing")

    def test_international_is_not_an_intern_and_manager_is_not_a_gate(self):
        self.assertFalse(early_signal({"title":"International Security Lead"}))
        self.assertTrue(early_signal({"title":"Events Internship"}))
        self.assertTrue(early_signal({"title":"Senior Office Administrator"}))
        self.assertEqual(evidence_tier({"title":"Director, Programs","experience_hint":"provider_early_career"}),0)

    def test_seeded_low_signal_review_and_title_employer_diversity(self):
        rows=[dict(source="test",source_id=str(i),url=f"https://example.org/{i}",title=("Community Navigator "+str(i) if i%2 else "Program Assistant "+str(i)),company="Employer "+str(i%6),location="Washington, DC") for i in range(50)]
        first=plan_inspections(rows,20,5)
        self.assertTrue(any(x["stratum"]=="low_signal_random_review" for x in first))
        self.assertEqual(first,plan_inspections([rows[0],*reversed(rows[1:])],20,5))
        self.assertNotEqual(first,plan_inspections(rows,20,6))
        self.assertEqual(len(first),20)

    def test_geography_suburbs_states_and_ambiguity(self):
        cases={"Camp Springs, MD":"dc_dmv","Forestville, MD":"dc_dmv","Upper Marlboro, Maryland":"dc_dmv",
               "Washington, D.C.":"dc_dmv","Herndon, Virginia":"dc_dmv","Arlington, TX":"elsewhere",
               "Seattle, Washington":"elsewhere","Charlottesville, Virginia":"elsewhere",
               "Richmond, VA":"elsewhere","based in Maryland":"regional_worksite_unknown",
               "Northern Virginia":"regional_worksite_unknown"}
        for loc,label in cases.items():
            with self.subTest(location=loc):self.assertEqual(geography(loc),label)

    def test_regional_state_only_location_is_retained_without_being_recommended(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);path=root/"input.csv"
            with path.open("w",newline="") as f:
                writer=csv.DictWriter(f,fieldnames=("title","company","location","url","source"));writer.writeheader()
                writer.writerow(dict(title="Policy Research Associate",company="Example",location="based in Maryland",url="https://example.org/job",source="csv"))
            args=["engine","--sources","csv","--import-csv",str(path),"--db",str(root/"jobs.db"),"--output",str(root/"brief.md")]
            with patch("sys.argv",args),redirect_stdout(io.StringIO()):self.assertEqual(main(),0)
            with (root/"screening_audit.csv").open() as f:rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]["geography"],"regional_worksite_unknown")
            self.assertNotEqual(rows[0]["decision"],"recommended")

    def test_real_snapshot_comparison_is_executable_and_all_selected_descriptions_observed(self):
        root=Path(__file__).resolve().parents[1]/"docs/issue5"
        data=json.loads((root/"records.json").read_text())
        measured=json.loads((root/"inspection-comparison.json").read_text())
        actual=compare(data,20,5)
        self.assertEqual(actual,{key:value for key,value in measured.items() if key!="capture_sha256"})
        self.assertEqual(measured["capture_sha256"],hashlib.sha256((root/"records.json").read_bytes()).hexdigest())
        self.assertEqual(len(data["records"]),327)
        self.assertEqual(sum(row["description_observed"] for row in data["records"]),95)
        self.assertEqual(sum(d["diverse"]["dc_dmv_selected"] for d in measured["sources"].values()),91)
        self.assertEqual(sum(d["prefix"]["dc_dmv_selected"] for d in measured["sources"].values()),70)
        for source,counts in measured["sources"].items():
            self.assertEqual(counts["diverse"]["planned"],counts["diverse"]["already_observed_descriptions"])
        self.assertIn("globaljobs:52105",measured["sources"]["globaljobs"]["newly_inspected_identities"])
        self.assertIn("globaljobs:52025",measured["sources"]["globaljobs"]["newly_inspected_identities"])

    def test_real_review_sample_covers_new_sectors_and_keeps_uncertainty(self):
        root=Path(__file__).resolve().parents[1]/"docs/issue5"
        data=json.loads((root/"records.json").read_text());sample=json.loads((root/"review-sample.json").read_text())
        ids={row["source"]+":"+row["source_id"] for row in data["records"]}
        self.assertGreaterEqual(len(sample["records"]),20)
        self.assertTrue(all(row["identity"] in ids for row in sample["records"]))
        self.assertEqual({row["assessment"] for row in sample["records"]},{"plausible","stretch","ineligible","unknown"})
        self.assertTrue(any("consulting" in row["sector"] for row in sample["records"]))
        self.assertTrue(any("think tank" in row["sector"] for row in sample["records"]))
        self.assertTrue(all(row["description_observed"] for row in sample["records"]))


if __name__=="__main__":unittest.main()
