"""M1b coverage, uncertainty, and offline replay regressions."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from career_engine.export import export_snapshot
from career_engine.geography import DEFAULT_VERSION, classify_geo
from career_engine.relabel import relabel
from career_engine.store import Store

ROOT = Path(__file__).resolve().parents[1]
TIME = "2026-10-01T12:00:00Z"
CODE_SHA = "a" * 40


class GeographyTests(unittest.TestCase):
    def check_cases(self, cases):
        for location, expected in cases.items():
            with self.subTest(location=location):
                value, reason = classify_geo(location)
                self.assertEqual(value, expected)
                self.assertTrue(reason)

    def test_reviewed_coverage_cases(self):
        source = ROOT / "docs/coverage/data/geography-cases.json"
        expected = {"strict_DC": "dc", "Washington_MSA": "dmv_va", "outside_Washington_MSA": "elsewhere"}
        self.check_cases({
            row["raw_location"]: expected[row["reviewed_expected_scope"]]
            for row in json.loads(source.read_text())["cases"]
        })

    def test_committed_regional_location_evidence_has_no_false_elsewhere(self):
        paths = ("docs/issue17/actual-local-records.jsonl", "docs/issue21/paired-regional-records.json")
        actual = []
        for name in paths:
            data = (ROOT / name).read_text()
            rows = ([json.loads(line) for line in data.splitlines() if line] if name.endswith(".jsonl")
                    else [row for rows in json.loads(data).values() if isinstance(rows, list) for row in rows])
            actual.extend(row["location"] for row in rows if row.get("geography") == "dc_dmv" and row.get("location"))
        self.assertTrue(actual)
        for location in set(actual):
            with self.subTest(location=location):
                self.assertNotEqual(classify_geo(location)[0], "elsewhere")

    @unittest.skipUnless(os.environ.get("PROSPECT_RUN8_ARCHIVE"), "set PROSPECT_RUN8_ARCHIVE to the original run-8 ZIP")
    def test_original_run8_locations_have_no_false_elsewhere(self):
        path = Path(os.environ["PROSPECT_RUN8_ARCHIVE"])
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(65536), b""):
                digest.update(chunk)
        self.assertEqual(digest.hexdigest(), "874c61ee40c8114cd27e74a7ab94a3fe768e8bec9e419f755b0c007b8c67dd24")
        with zipfile.ZipFile(path) as archive, archive.open("dc-expanded-audit.json") as audit:
            records = json.load(audit)["records"]
        regional = [row["location"] for row in records if row.get("geography") == "dc_dmv" and row.get("location")]
        self.assertEqual((len(records), len(regional), len(set(regional))), (3288, 216, 74))
        calculations = json.loads((ROOT / "docs/coverage/calculations.json").read_text())
        reviewed = set(calculations["engine"]["latent_locations"])
        self.assertEqual(len(reviewed), 15)
        self.assertLessEqual(reviewed, {row.get("location") for row in records})
        for location in set(regional) | reviewed:
            with self.subTest(location=location):
                self.assertNotEqual(classify_geo(location)[0], "elsewhere")

    def test_place_disambiguation_and_unsettled_boundaries(self):
        self.check_cases({
            "": "unknown", "Washington": "unknown", "Arlington": "unknown",
            "Alexandria": "unknown", "Columbia": "unknown", "Columbia, MD": "unknown",
            "Herdon, VA": "unknown", "Maryland or Texas": "unknown",
            "Arlington, TX ; Virginia": "unknown", "Arlington, VA, TX": "unknown",
            "Washington County, MD": "unknown", "Frederick, MD": "unknown",
            "Fredericksburg, Virginia": "unknown", "Quantico, VA": "unknown",
            "Stafford, VA": "unknown", "DMV": "unknown", "Northern Virginia": "unknown",
            "Washington, D.C.": "dc", "Washington, DC OR Reston, VA": "dc",
            "US-DC-Washington": "dc", "D C": "dc", "Arlington, VA Pentagon City": "dmv_va",
            "Alexandria, VA": "dmv_va", "Arlington County, Virginia": "dmv_va",
            "Arlington, TX | Arlington, VA": "dmv_va", "Fort Belvoir, VA, VA": "dmv_va",
            "Ft. Belvoir, VA": "dmv_va", "Woodbridge, VA F - Huber": "dmv_va",
            "Bethesda": "dmv_md", "Silver Spring, MD": "dmv_md", "Camp Springs, MD": "dmv_md",
            "Gaithersburg, MD Gaithersburg": "dmv_md", "Beltsville, MD Maryland": "dmv_md",
            "US-MD-Landover": "dmv_md", "Washington State": "elsewhere",
            "US-WA-Washington": "elsewhere", "Washington, PA": "elsewhere",
            "Seattle, Washington": "elsewhere", "Arlington, TX": "elsewhere",
            "Columbia, SC": "elsewhere", "Alexandria, LA": "elsewhere",
            "Baltimore, MD": "elsewhere", "Richmond, VA": "elsewhere", "London, UK": "elsewhere",
        })
        value, reason = classify_geo("Arlington", {"state": "VA"})
        self.assertEqual(value, "dmv_va")
        self.assertIn("Virginia", reason)
        self.assertEqual(classify_geo("Washington", {"state": "DC"})[0], "dc")

    def test_remote_requires_evidence_that_dc_residents_are_eligible(self):
        self.check_cases({
            "Remote": "remote_eligibility_unknown", "Anywhere": "remote_eligibility_unknown",
            "Remote; Germany": "remote_eligibility_unknown", "US-DC-Remote": "remote_eligibility_unknown",
            "Remote US, eligible states only": "remote_eligibility_unknown",
            "Remote in the US except Washington": "remote_eligibility_unknown",
            "United States": "unknown", "Telework eligible": "unknown",
            "Arlington, VA (not remote)": "dmv_va",
            "Remote; USA": "remote_us_eligible", "U.S. Remote": "remote_us_eligible",
            "Anywhere in the United States": "remote_us_eligible", "Worldwide": "remote_us_eligible",
            "Remote in Washington, DC": "remote_us_eligible",
            "Remote in the United States excluding Washington, DC": "elsewhere",
            "Remote, Canada only": "elsewhere",
        })
        self.assertEqual(classify_geo("Remote", {"country": "US"})[0], "remote_eligibility_unknown")
        self.assertEqual(classify_geo("Remote", {"remote_scope": "US only"})[0], "remote_us_eligible")
        self.assertEqual(classify_geo("Remote", {"remote_scope": "Washington, DC"})[0], "remote_us_eligible")

    def test_conflicting_location_evidence_remains_uncertain(self):
        fixture = ROOT / "tests/fixtures/issue14/actual-location-conflict.json"
        record = json.loads(fixture.read_text())["record"]
        value, reason = classify_geo(record["location"], {"title": record["title"]})
        self.assertEqual(value, "unknown")
        self.assertIn("conflict", reason)
        self.assertEqual(classify_geo("London, UK", {"worksites": ["Arlington, VA"]})[0], "unknown")


    def test_structured_geography_disagreements_preserve_uncertainty(self):
        cases = (
            ("Baltimore, MD", {"state": "DC"}),
            ("Washington, DC", {"state": "VA"}),
            ("Washington, DC", {"country": "Canada"}),
            ("Washington, DC", {"country": "CA"}),
            ("London, UK", {"country": "United States"}),
            ("Baltimore, MD", {"worksites": ["Silver Spring, MD"]}),
            ("Arlington, TX", {"worksites": ["Arlington, VA"]}),
            ("Arlington", {"worksites": ["Dallas, TX"]}),
            ("Quantico, VA", {"worksites": ["Richmond, VA"]}),
            ("Washington, DC", {"worksites": ["Arlington, VA"]}),
            ("Washington, DC, Canada", {}),
            ("Baltimore, MD", {"worksites": [{"location": "Baltimore, MD", "state": "DC"}]}),
            ("Baltimore, MD", {"worksites": [{"location": "Washington, DC", "country": "Canada"}]}),
            ("Remote, Canada only", {"worksites": ["Washington, DC"]}),
            ("Remote, Canada only", {"state": "DC"}),
            ("Remote in the US excluding DC", {"state": "DC"}),
        )
        for location, context in cases:
            with self.subTest(location=location, context=context):
                value, reason = classify_geo(location, context)
                self.assertEqual(value, "unknown")
                self.assertIn("conflict", reason)

    def test_agreement_and_distinct_worksite_options_remain_usable(self):
        self.assertEqual(classify_geo("Baltimore, MD", {"state": "MD", "country": "US"})[0], "elsewhere")
        self.assertEqual(classify_geo("London, UK", {"country": "GB"})[0], "elsewhere")
        self.assertEqual(classify_geo("Arlington", {"state": "Virginia"})[0], "dmv_va")
        self.assertEqual(classify_geo("Washington, DC | Baltimore, MD", {"state": "DC"})[0], "dc")
        self.assertEqual(classify_geo("Washington, DC", {"worksites": ["Washington, DC", "Arlington, VA"]})[0], "dc")

class ReplayTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.store = Store.in_data_dir(self.data, clock=lambda: TIME)
        self.addCleanup(self.store.__exit__)
        self.run_id = self.store.begin_run(code_sha=CODE_SHA, config_sha="b" * 64)

    def add_poll(self, platform, entries, capture=None):
        channel = self.store.ensure_channel(
            kind="api", platform=platform, locator=f"https://{platform}.example/",
            retention_class="public", evidence_role="discovery", verification_state="verified",
        )
        sha = None
        if capture is not None:
            body = json.dumps(capture).encode()
            sha = self.store.blobs.put(body, "public")
            self.store.register_capture(sha, len(body))
        poll = self.store.record_poll(self.run_id, channel, status="ok", received=len(entries), raw_sha=sha)
        for native, location in entries:
            url = f"https://{platform}.example/jobs/{native}"
            self.store.record_sighting(
                poll, sighting_id=f"{platform}-{native}", identity_key=f"{platform}:synthetic:{native}",
                native_id=native, url=url, canonical_url=url, title="Synthetic role", employer_name_raw="Example",
                location_raw=location, raw_sha=sha,
            )

    def test_replay_uses_only_the_matching_posting_in_a_capture(self):
        rows = [
            {"MatchedObjectId": "1", "MatchedObjectDescriptor": {
                "PositionLocation": [{"CityName": "Arlington", "CountrySubDivisionCode": "VA", "CountryCode": "US"}],
            }},
            {"MatchedObjectId": "2", "MatchedObjectDescriptor": {
                "PositionLocation": [{"CityName": "Arlington", "CountrySubDivisionCode": "TX", "CountryCode": "US"}],
            }},
        ]
        self.add_poll("usajobs", [("1", "Arlington"), ("2", "Arlington"), ("3", "Arlington")],
                      {"SearchResult": {"SearchResultItems": rows}})
        self.add_poll("greenhouse", [("4", "Unspecified")], {
            "jobs": [{"id": 4, "offices": [{"location": "Bethesda, MD"}]}],
        })
        self.add_poll("greenhouse", [("5", "Washington")], {"jobs": [{"id": 5, "location": {"name": "DC"}}]})
        sha = self.store.connection.execute("SELECT raw_sha FROM sighting WHERE sighting_id = 'greenhouse-5'").fetchone()[0]
        path, = self.data.glob(f"blobs/public/*/{sha}.gz")
        path.unlink()
        self.assertEqual(relabel(self.store, "geo", DEFAULT_VERSION), 5)
        values = dict(self.store.connection.execute("SELECT subject_id, value FROM label"))
        self.assertEqual(values, {"usajobs-1": "dmv_va", "usajobs-2": "elsewhere", "usajobs-3": "unknown",
                                  "greenhouse-4": "dmv_md", "greenhouse-5": "unknown"})

    def test_replay_keeps_conflicting_structured_address_fields_uncertain(self):
        rows = [
            {"MatchedObjectId": "1", "MatchedObjectDescriptor": {
                "PositionLocation": [{"LocationName": "Baltimore, MD", "CountrySubDivisionCode": "DC",
                                      "CountryCode": "US"}],
            }},
            {"MatchedObjectId": "2", "MatchedObjectDescriptor": {
                "PositionLocation": [{"LocationName": "Washington, DC", "CountrySubDivisionCode": "DC",
                                      "CountryCode": "Canada"}],
            }},
        ]
        self.add_poll("usajobs", [("1", "Baltimore, MD"), ("2", "Washington, DC")],
                      {"SearchResult": {"SearchResultItems": rows}})
        self.assertEqual(relabel(self.store, "geo", DEFAULT_VERSION), 2)
        for value, explanation in self.store.connection.execute("SELECT value, method FROM label"):
            self.assertEqual(value, "unknown")
            self.assertIn("conflict", explanation)

    def test_command_preserves_old_label_and_is_idempotent(self):
        self.add_poll("usajobs", [("1", "US-DC")], {"SearchResult": {"SearchResultItems": []}})
        self.store.end_run(self.run_id)
        self.store.add_label(subject="sighting", subject_id="usajobs-1", dimension="geo",
                             value="unknown", method="historical", classifier_version="gazetteer-dc/0",
                             created_at=TIME)
        command = [sys.executable, "-m", "career_engine.relabel", "--data-dir", str(self.data),
                   "--dimension", "geo", "--version", DEFAULT_VERSION]
        for expected in (1, 0):
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"Appended {expected} geo labels", result.stdout)
        labels = list(self.store.connection.execute("SELECT value, method, classifier_version FROM label ORDER BY label_id"))
        self.assertEqual(tuple(labels[0]), ("unknown", "historical", "gazetteer-dc/0"))
        self.assertEqual((labels[1]["value"], labels[1]["classifier_version"]), ("dc", DEFAULT_VERSION))
        self.assertIn("District of Columbia", labels[1]["method"])
        bundle = export_snapshot(self.store, self.data / "exports", code_sha=CODE_SHA,
                                 created_at="2026-11-18T14:10:00Z")
        exported = [json.loads(line) for line in (bundle / "labels.jsonl").read_bytes().splitlines()]
        self.assertEqual({row["classifier_version"] for row in exported}, {"gazetteer-dc/0", DEFAULT_VERSION})

    def test_concurrent_replays_append_each_label_once(self):
        self.add_poll("usajobs", [("1", "Arlington, VA"), ("2", "Remote")])

        def run():
            with Store.in_data_dir(self.data, create=False) as store:
                return relabel(store, "geo", DEFAULT_VERSION)

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = [workers.submit(run) for _ in range(2)]
            self.assertEqual(sorted(future.result() for future in results), [0, 2])
        self.assertEqual(self.store.connection.execute("SELECT count(*) FROM label").fetchone()[0], 2)

    def test_unknown_version_does_not_write(self):
        self.add_poll("usajobs", [("1", "Washington, DC")])
        with self.assertRaisesRegex(ValueError, "Unknown geography classifier version"):
            relabel(self.store, "geo", "gazetteer-dc/not-released")
        self.assertEqual(self.store.connection.execute("SELECT count(*) FROM label").fetchone()[0], 0)
