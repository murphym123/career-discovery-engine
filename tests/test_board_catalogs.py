from collections import Counter
from hashlib import sha256
import io
import json
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from career_engine.ats_registry import register, select_boards, validate_registry
from career_engine.board_catalogs import CATALOG_CAMPAIGN, DATA_BYTES, POLICY_BYTES, ROBOTS_URL, CatalogTransport, import_catalogs
from career_engine.board_discovery import DELAYS, Discovery, Limits
from career_engine.board_discovery_state import DiscoveryState
from career_engine.board_indexes import Page
from career_engine.regional_selection import select_regional_boards
from career_engine.registry_state import read_registry, save_registry
from career_engine.store import Store
from test_board_discovery import FixtureTransport, PLAN, reached, wire
from tools.expand_ats import expand
from tools.import_board_catalog import main


HEADER = b"ats_vendor,company_name,board_slug,last_crawled\n"
CONTACT = "catalog-fixtures@example.invalid"
REVISION = "1234567890abcdef" * 2 + "12345678"
LAST_ROUND_URL = ("https://raw.githubusercontent.com/fyrosofttech/lastroundai-hiring-data/"
                  "{}/ats-directory/lastroundai-ats-company-directory-2026-08.csv")
MOONIE_BASE = "https://raw.githubusercontent.com/moonie0201/ats-directory/{}/"


def jsonl(*rows):
    return b"".join(json.dumps(row).encode() + b"\n" for row in rows)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.data = self.root / "data"
        self.registry = self.data / "ats-registry.json"
        self.open_store()
        self.addCleanup(lambda: self.store.connection.close())
        network = patch("requests.adapters.HTTPAdapter.send", side_effect=AssertionError("Live HTTP from fixture test"))
        network.start()
        self.addCleanup(network.stop)
        self.now = PLAN.through.timestamp()

    def open_store(self):
        self.store = Store.in_data_dir(self.data)
        self.state = DiscoveryState(self.store)

    def write_pin(self, name, body):
        (self.root / name).write_bytes(body)
        return {"file": name, "sha256": sha256(body).hexdigest()}

    def manifest(self, csv=None, moon=None, blocklist=b"", policy=None):
        config = {}
        if csv is not None:
            config["last-round-ai"] = self.write_pin("last-round.csv", csv)
        if moon is not None:
            config["moonie"] = self.write_pin("moonie.jsonl", moon)
            config["moonie"].update({f"blocklist_{k}": v for k, v in self.write_pin("blocklist.txt", blocklist).items()})
        if policy is not None:
            config["policy"] = self.write_pin("policy.txt", policy)
        return self.save_manifest(config)

    def save_manifest(self, config):
        path = self.root / "manifest.json"
        path.write_text(json.dumps(config))
        return path

    def cli(self, *args):
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            code = main(["--data-dir", str(self.data), *map(str, args)])
        return code, json.loads(out.getvalue())

    def validate(self, handler, count=30):
        transport = FixtureTransport(handler, lambda: self.now)
        limits = Limits(count, 1, concurrency=4, origin_delays=dict.fromkeys(DELAYS, 0))
        result = Discovery(self.state, transport, CATALOG_CAMPAIGN, None, limits, mode="validate",
                           clock=lambda: self.now, monotonic=lambda: self.now, sleep=self.advance).run()
        return result, transport.calls

    def advance(self, seconds):
        self.now += seconds

    def test_full_synthetic_last_round_catalog_through_offline_import_validate_and_publish(self):
        counts = {"greenhouse": 4_966, "ashby": 2_856, "lever": 2_113}
        body = HEADER + b"".join(
            f"{vendor},Example {number},{vendor}-fixture-{number},2026-08-10\n".encode()
            for vendor, count in counts.items() for number in range(count)
        )
        manifest = self.manifest(csv=body)
        self.store.connection.close()
        self.data.joinpath("prospect.sqlite").unlink()
        code, imported = self.cli("import", "--offline", "--manifest", manifest)
        self.assertEqual(code, 0)
        self.assertEqual(stat.S_IMODE((self.data / "prospect.sqlite").stat().st_mode), 0o600)
        self.open_store()
        self.assertEqual(imported["boards"]["candidate"], 9_935)
        self.assertEqual(imported["boards"]["validated"], 0)
        batch, = imported["imports"]
        self.assertEqual(batch["counts"]["rows"], 9_935)
        self.assertEqual(batch["counts"]["providers"], counts)
        self.assertEqual(batch["license"], "CC-BY-4.0")
        self.assertEqual(batch["url"], LAST_ROUND_URL.format("main"))
        self.assertEqual(batch["sha256"], sha256(body).hexdigest())
        self.assertEqual(self.cli("publish")[1]["registry_boards"], 0)

        transport = FixtureTransport(lambda *args: wire(status=404) if args[1].path == "/robots.txt" else reached(),
                                     lambda: self.now)
        with patch("tools.discover_boards.RequestsTransport") as factory:
            factory.return_value.__enter__.return_value = transport
            _, validated = self.cli("validate", "--contact", CONTACT, "--max-requests", 10_000, "--concurrency", 4,
                                    "--origin-delay", "api.lever.co=0", "--origin-delay", "api.ashbyhq.com=0",
                                    "--origin-delay", "boards-api.greenhouse.io=0")
        self.assertEqual(validated["boards"]["validated"], 9_935)
        self.assertEqual(validated["requests"], 9_938)
        self.assertEqual(validated["page_requests"], 0)
        self.assertEqual(self.cli("publish")[1]["added"], 9_935)
        self.assertEqual(self.cli("publish")[1]["added"], 0)
        registry = read_registry(self.registry)
        self.assertEqual(Counter(board["provider"] for board in registry["boards"].values()), counts)
        evidence, = registry["boards"]["ashby:ashby-fixture-2855"]["evidence"]
        self.assertEqual(evidence["attribution"], "LastRound AI")
        self.assertEqual(evidence["attribution_url"], "https://lastroundai.com/blog/salary-transparency-study-2026")
        self.assertEqual(evidence["record_date"], "2026-08-10")
        self.assertNotIn("archive_timestamp", evidence)
        # A production registry must retain every verified entry after normal validation.
        self.assertEqual(len(validate_registry(registry)["boards"]), 9_935)
        self.assertTrue(register(registry, "https://job-boards.greenhouse.io/beyond9350",
                                 {"kind": "source_audit_link", "url": "https://example.invalid"}))
        self.assertEqual(len(validate_registry(registry)["boards"]), 9_936)

    def test_mixed_sources_aliases_statuses_provenance_and_takedowns(self):
        csv = HEADER + (b"greenhouse,First spelling,ExampleCo,2026-07-13\n"
                        b"lever,US company,same,2026-07-19\n"
                        b"ashby,Policy block,withhold,2026-07-19\n"
                        b"ashby,Local block,local-block,2026-07-19\n"
                        b"ashby,Withdrawn,withdraw-me,2026-07-19\n")
        moon = jsonl(
            {"ats": "greenhouse", "slug": "exampleco", "name": "Second spelling", "status": "ok",
             "verified_at": "2026-08-09", "board_url": "https://boards.greenhouse.io/exampleco?token=omit",
             "sources": [{"url": "https://example.org/careers?a=secret#private", "kind": "employer"}]},
            {"provider": "lever", "board_slug": "same", "company_name": "EU company", "region": "EU", "status": "unconfirmed"},
            {"ats": "ashby", "slug": "old", "status": "dead"},
            {"ats": "ashby", "slug": "withdraw-me", "status": "withdrawn"},
            {"ats": "ashby", "slug": "withhold", "status": "ok"},
            {"ats": "recruitee", "slug": "one", "status": "ok"},
            {"ats": "rippling", "slug": "two", "status": "unconfirmed"},
            {"ats": "rippling", "slug": "removed", "status": "ok"},
            {"ats": "ashby", "slug": "invalid-status", "status": "live"},
            *({"ats": "ashby", "slug": value, "status": "ok"} for value in
              ("//other.org/path", "a%2Fb", "a%252f", "a?key=z", "a#fragment", "..", "a\\b", "a\x00b")),
        ) + b'{"slug": "invalid", "slug": "duplicate"}\n'
        manifest = self.manifest(csv, moon, b"# Publisher opt-outs\nashby:WITHHOLD\nhttps://ats.rippling.com/removed/jobs\n",
                                 b"ashby/local-block\n")
        result = import_catalogs(self.state, manifest, offline=True)
        self.assertEqual(result["boards"]["candidate"], 3)
        self.assertEqual(result["evidence"], 7)
        self.assertEqual(result["unsupported"], {"recruitee": 1, "rippling": 1})
        self.assertEqual(self.cli("status", "--unsupported")[1]["unsupported_observations"][1]["raw_slug"], "two")
        moon_batch = result["imports"][1]
        self.assertEqual(moon_batch["license"], "CC0-1.0")
        self.assertEqual(moon_batch["counts"]["rejected"], {"invalid_status": 1, "invalid_slug": 8, "invalid_row": 1})
        self.assertEqual(moon_batch["counts"]["blocked"], 3)
        self.assertEqual(import_catalogs(self.state, manifest, offline=True), result)

        def handler(method, url, params):
            if url.path == "/robots.txt" or url.path.endswith("/ExampleCo"):
                return wire(status=404)
            self.assertNotEqual(url.hostname, "api.ashbyhq.com")
            self.assertEqual(method, "HEAD")
            return reached()

        validated, calls = self.validate(handler)
        self.assertEqual(validated["boards"]["validated"], 3)
        self.assertIn(("HEAD", "api.eu.lever.co", "/v0/postings/same"), [call[:3] for call in calls])
        self.assertEqual(self.state.publish(self.registry, catalog_only=True)["added"], 3)
        registered = read_registry(self.registry)
        entry = registered["boards"]["greenhouse:exampleco"]
        self.assertEqual(entry["token"], "exampleco")
        last_round, moonie = entry["evidence"]
        self.assertEqual((last_round["raw_slug"], moonie["raw_slug"]), ("ExampleCo", "exampleco"))
        self.assertEqual(moonie["observed_url"], "https://boards.greenhouse.io/exampleco")
        self.assertEqual(moonie["source_evidence"]["sources"], [{"url": "https://example.org/careers", "kind": "employer"}])
        self.assertEqual(entry["discovery_verification"]["catalog_evidence_id"], moonie["catalog_evidence_id"])
        self.assertEqual(registered["catalog_blocked_boards"]["ashby:withdraw-me"], True)

        changed = self.manifest(csv, moon, b"greenhouse:exampleco\n")
        import_catalogs(self.state, changed, offline=True)
        self.state.publish(self.registry, catalog_only=True)
        updated = read_registry(self.registry)
        self.assertIn("greenhouse:exampleco", updated["catalog_blocked_boards"])
        self.assertEqual(updated["boards"]["greenhouse:exampleco"]["evidence"], [])
        self.assertNotIn("discovery_verification", updated["boards"]["greenhouse:exampleco"])
        self.assertNotIn("greenhouse:exampleco", {row["key"] for row in select_boards(updated)})
        self.assertNotIn("greenhouse:exampleco", {row["key"] for row in select_regional_boards(updated)})
        self.assertEqual(list(self.state._catalog_proofs("greenhouse:exampleco")), [])

    def test_archive_queue_is_separate_and_validated_evidence_can_be_reused(self):
        self.state.setup("archive", PLAN)
        stream = next(s for s in self.state.streams("archive") if s["host"] == "boards.greenhouse.io")
        page = Page([(f"https://boards.greenhouse.io/{slug}", "20260101000000", "") for slug in ("shared", "archive")], None)
        self.state.record_page(stream, page, self.store.now())
        variants = list(self.state.variants("boards-api.greenhouse.io", 0))
        self.state.set_variant(variants[0], "validated", "head_reachable", "HEAD", self.store.now())
        pinned = self.manifest(csv=HEADER + b"greenhouse,Shared,shared,2026-08-01\n")
        result = import_catalogs(self.state, pinned, offline=True)
        self.assertEqual(result["boards"]["validated"], 1)
        self.assertEqual(result["boards"]["candidate"], 0)
        result, calls = self.validate(lambda *_: self.fail("Catalog validation called an archive-only board"))
        self.assertEqual(calls, [])
        self.state.publish(self.registry, catalog_only=True)
        entry = read_registry(self.registry)["boards"]["greenhouse:shared"]
        self.assertEqual([proof["kind"] for proof in entry["evidence"]], ["internet_archive_url_index", "public_ats_catalog"])

    def test_validation_resumes_after_limited_runs_and_honors_robots_and_stopped_origins(self):
        manifest = self.manifest(HEADER + (b"greenhouse,Allowed,allowed,2026-07-19\n"
                                           b"greenhouse,Denied,denied,2026-07-19\n"
                                           b"greenhouse,Missing,gone,2026-07-19\n"
                                           b"lever,Stopped,stopped,2026-07-19\n"))
        import_catalogs(self.state, manifest, offline=True)
        self.state.set_origin("api.lever.co", 0, "challenge")

        def handler(method, url, params):
            self.assertEqual(url.hostname, "boards-api.greenhouse.io")
            if url.path == "/robots.txt":
                return wire(b"User-agent: *\nDisallow: /v1/boards/denied\n")
            self.assertFalse(url.path.endswith("/denied"))
            if url.path.endswith("/gone"):
                return wire(status=410)
            return wire(status=405) if method == "HEAD" else wire({"name": "Allowed"})

        first, calls = self.validate(handler, count=2)
        self.assertEqual(first["stop_reason"], "request_budget")
        self.assertEqual([call[0] for call in calls], ["GET", "HEAD"])
        self.store.connection.close()
        self.open_store()
        resumed, calls = self.validate(handler)
        self.assertEqual(resumed["boards"], {"validated": 1, "candidate": 0, "missing": 1, "denied": 2,
                                             "failed": 0, "deferred": 0})
        self.assertEqual({call[0] for call in calls}, {"GET", "HEAD"})
        self.assertNotIn("/robots.txt", [call[2] for call in calls])
        attempts = [tuple(row) for row in self.state.db.execute(
            "SELECT status,reason FROM board_discovery_attempt WHERE campaign=?", (CATALOG_CAMPAIGN,))]
        self.assertIn(("denied", "robots: disallow: /v1/boards/denied"), attempts)
        self.assertIn(("validated", "get_board_schema"), attempts)
        self.assertIn(("missing", "http_410"), attempts)

    def test_poll_and_registry_reseeding_preserve_catalog_proof_and_prior_fields(self):
        path = self.manifest(HEADER + b"greenhouse,Test Company,pollable,2026-08-01\n")
        import_catalogs(self.state, path, offline=True)
        self.validate(lambda method, url, params: wire(status=404) if url.path == "/robots.txt" else reached())
        self.state.publish(self.registry, catalog_only=True)
        registry = read_registry(self.registry)
        for n in range(24):
            register(registry, "https://boards.greenhouse.io/pollable", {"kind": "source_audit_link", "source_id": str(n)})
        save_registry(self.registry, registry)

        class EmptyGreenhouse:
            def __init__(self):
                self.session = object()
                self.requests, self.publication_observations = [], []
                self.bytes, self.max_requests = 0, 10

            def global_stop_reason(self):
                return ""

            def json(self, url, params=None, **kwargs):
                self.requests.append({"url": url, "status": 200})
                return {"jobs": [], "meta": {"total": 0}} if url.endswith("/jobs") else {"name": "Test Company"}

        transport = EmptyGreenhouse()
        base = self.root / "baseline.json"
        base.write_text('{"records":[],"stats":{}}')
        with patch.dict("os.environ", {"PROSPECT_DATA_DIR": ""}):
            audit = expand(base, self.registry, self.root / "audit", self.root / "combined.json",
                           board_budget=1, seed_queries=0, transport=transport, extra_details=0)
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(audit["schema_version"], "job-source-audit/v1")
        self.assertEqual(audit["discovery"]["boards"][0]["status"], "ok")
        self.assertEqual(self.state.publish(self.registry, catalog_only=True)["updated"], 0)
        entry = read_registry(self.registry)["boards"]["greenhouse:pollable"]
        self.assertEqual((entry["last_checked_run"], entry["status"]), (1, "ok"))
        self.assertTrue(any(item["kind"] == "public_ats_catalog" for item in entry["evidence"]))

    def test_manifest_pins_reject_mismatch_in_either_object_and_unsafe_inputs(self):
        good = self.manifest(HEADER + b"lever,Example,one,2026-08-01\n", jsonl({"ats": "ashby", "slug": "two", "status": "ok"}))
        (self.root / "blocklist.txt").write_bytes(b"changed\n")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            import_catalogs(self.state, good, offline=True)
        self.assertEqual(self.state.db.execute("SELECT count(*) FROM board_catalog_import").fetchone()[0], 0)
        cases = [
            ({"last-round-ai": {"url": "https://other.invalid/file"}}, "manifest fields"),
            ({"last-round-ai": {"revision": "main/../malicious"}}, "full Git commit"),
            ({"last-round-ai": {}}, "expected SHA-256"),
            ({"last-round-ai": {"revision": REVISION}}, "Offline imports"),
        ]
        for config, message in cases:
            with self.subTest(config=config), self.assertRaisesRegex(ValueError, message):
                import_catalogs(self.state, self.save_manifest(config), offline=True)

    def test_remote_artifacts_use_same_pinned_moonie_revision_and_fixed_object_limits(self):
        manifest = self.save_manifest({"last-round-ai": {"revision": REVISION}, "moonie": {"revision": REVISION}})
        moonie = MOONIE_BASE.format(REVISION)
        replies = {
            ROBOTS_URL: wire(b"User-agent: *\nCrawl-delay: 1\n"),
            LAST_ROUND_URL.format(REVISION): wire(HEADER + b"lever,Example,one,2026-08-01\n"),
            moonie + "blocklist.txt": wire(b"# blank\n"),
            moonie + "companies.jsonl": wire(jsonl({"ats": "ashby", "slug": "two", "status": "ok"})),
        }
        calls = []

        def mock(method, url, limit, seconds):
            calls.append((method, url, limit, self.now))
            return replies[url]

        with patch.object(CatalogTransport, "request", side_effect=mock):
            result = import_catalogs(self.state, manifest, contact=CONTACT, clock=lambda: self.now, sleep=self.advance)
        self.assertEqual(result["boards"]["candidate"], 2)
        self.assertEqual(result["imports"][1]["blocklist_sha256"], sha256(b"# blank\n").hexdigest())
        self.assertEqual([cap for _, _, cap, _ in calls], [512_000, DATA_BYTES, POLICY_BYTES, DATA_BYTES])
        self.assertEqual([url for _, url, _, _ in calls], list(replies))
        self.assertEqual([epoch - calls[0][3] for _, _, _, epoch in calls], [0, 1, 2, 3])

    def test_remote_robots_denial_and_challenge_do_not_download_or_retry(self):
        manifest = self.save_manifest({"last-round-ai": {"revision": REVISION}})
        with patch.object(CatalogTransport, "request", return_value=wire(
                b"User-agent: *\nDisallow: /fyrosofttech/\n")) as request:
            with self.assertRaisesRegex(ValueError, "denied by robots"):
                import_catalogs(self.state, manifest, contact=CONTACT)
            request.assert_called_once_with("GET", ROBOTS_URL, 512_000, 30)
        with patch.object(CatalogTransport, "request", side_effect=[wire(status=404), wire(cf_mitigated="challenge")]) as request:
            with self.assertRaisesRegex(ValueError, "access stopped"):
                import_catalogs(self.state, manifest, contact=CONTACT)
            self.assertEqual(request.call_count, 2)
            request.reset_mock()
            with self.assertRaisesRegex(ValueError, "origin is stopped"):
                import_catalogs(self.state, manifest, contact=CONTACT)
            request.assert_not_called()
        self.assertEqual(self.state.db.execute("SELECT count(*) FROM board_catalog_import").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
