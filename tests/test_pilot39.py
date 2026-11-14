"""Safety boundary and durable-store tests for the fixture-only pilot."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from career_engine.ats import FetchError, board_from_url
from career_engine.ats_registry import empty_registry, register
from career_engine.export import export_snapshot
from career_engine.store import Store
from tools import pilot39 as pilot

GREEN = board_from_url("https://job-boards.greenhouse.io/example")
LEVER = board_from_url("https://jobs.lever.co/example")
JAZZ = board_from_url("https://synthetic-jazz.applytojob.com/apply")
GH_HOST = "boards-api.greenhouse.io"
LEVER_HOST = "api.lever.co"
JAZZ_HOST = "synthetic-jazz.applytojob.com"
LEV_PATH = "/v0/postings/example"
LEV_QUERY = {"mode": "json", "limit": 100, "skip": 0}
JOBS = "/v1/boards/example/jobs"
META = "/v1/boards/example"
SHA = "a" * 40
FIXTURES = Path(__file__).parent / "fixtures" / "jazzhr"


def address(host, path, query=None):
    return f"https://{host}{path}" + ("?" + urlencode(query) if query else "")


def body(value, media="application/json"):
    if media == "application/json":
        value = json.dumps(value)
    return pilot.Reply(media=media, chunks=((0, value.encode()),))


def robots(host, text="User-agent: *\nDisallow:\n"):
    return address(host, "/robots.txt"), body(text, "text/plain")


class PilotTests(unittest.TestCase):
    def setUp(self):
        for operation in ("connect", "connect_ex"):
            guard = patch.object(socket.socket, operation, side_effect=AssertionError("Network disabled"))
            guard.start()
            self.addCleanup(guard.stop)

    def transport(self, script=(), terms=(), board=GREEN):
        wire = pilot.FixtureWire(script)
        transport = pilot.PilotTransport(wire, terms)
        if board:
            transport.stage_for_board("seeded", board, 1)
        return transport, wire

    def test_offline_boundary_and_no_repeat_or_live_command(self):
        with self.assertRaises(TypeError):
            pilot.PilotTransport(object(), ())
        for args in (["collect"], ["precheck", "--repeat"]):
            with patch.object(sys, "argv", ["pilot39", *args]), patch("sys.stderr"):
                with self.assertRaises(SystemExit) as exc:
                    pilot.main()
                self.assertEqual(exc.exception.code, 2)

    def test_budgets_are_fixed_and_do_not_roll_over(self):
        self.assertEqual(pilot.STAGE_LIMITS, dict(terms=16, discovery=10, seeded=96, cold=26, repeat=12))
        budget = pilot.Budget()
        for stage, limit in pilot.STAGE_LIMITS.items():
            for _ in range(0 if stage == "repeat" else limit):
                budget.attempt(stage)
            with self.assertRaisesRegex(FetchError, "request_budget"):
                budget.attempt(stage)
        self.assertEqual(sum(budget.counts.values()), 148)
        with patch.object(pilot, "TOTAL_LIMIT", 1):
            budget = pilot.Budget()
            budget.attempt("discovery")
            with self.assertRaisesRegex(FetchError, "request_budget"):
                budget.attempt("seeded")

    def test_jazzhr_inventory_keeps_private_and_challenge_stops(self):
        fixture = (FIXTURES / "projectable-index.html").read_text()
        private_form = (FIXTURES / "private-detail.html").read_text().split("<form>", 1)[1]
        cases = (
            ("<form>" + private_form, pilot.SOURCE_PRIVATE, ""),
            ('<form><input type="password"></form>', pilot.CHALLENGE, ""),
            ('<h1>Sign in</h1>', pilot.CHALLENGE, ""),
            ('<form action="/account/login"></form>', pilot.CHALLENGE, ""),
            ('<script>const applicantId = "fictional-applicant-id";</script>', pilot.SOURCE_PRIVATE, ""),
            ('<div data-applicant-profile="fictional-profile"></div>', pilot.SOURCE_PRIVATE, ""),
            ('<script>window.accessToken = "fictional-access-token";</script>', pilot.SOURCE_PRIVATE, ""),
            ('<script>window["session-token"] = "fictional-session";</script>', pilot.SOURCE_PRIVATE, ""),
            ('<script type="application/json">{"applicantProfile":{"name":"Fictional Person"}}</script>',
             pilot.PRIVATE, pilot.PRIVATE),
            ('<script type="application/json">{"csrf-token":"\\u0066ictional-configured-value"}</script>',
             pilot.PRIVATE, pilot.PRIVATE),
            ('<script type="application/json">{invalid}</script>', "unreadable_public_html", ""),
            ('<meta content="Bearer fictional-bearer-token">', pilot.PRIVATE, pilot.PRIVATE),
            ('<a href="https://example.com/?signature=fictional-signed-value">Signed</a>',
             pilot.PRIVATE, pilot.PRIVATE),
            ('<a href="/apply/AbC01234/Analyst?signature=fictional-signed-value">Signed</a>',
             pilot.PRIVATE, pilot.PRIVATE),
        )
        for extra, reason, stop in cases:
            with self.subTest(extra=extra), patch.dict(os.environ, {"PROSPECT_TEST_SECRET": "fictional-configured-value"}):
                html = fixture.replace("<main>", "<main>" + extra)
                transport, _ = self.transport(
                    [robots(JAZZ_HOST), (JAZZ["url"], body(html, "text/html"))],
                    {(JAZZ_HOST, "/apply")}, JAZZ,
                )
                capture = Mock()
                with self.assertRaisesRegex(FetchError, reason):
                    transport.get(JAZZ["url"], capture=capture)
                capture.assert_not_called()
                self.assertEqual(transport.global_stop_reason(), stop)
                self.assertEqual(transport.blocked.get(JAZZ_HOST), pilot.CHALLENGE if reason == pilot.CHALLENGE else None)
                self.assertNotIn("sha256", transport.requests[-1])
                self.assertNotIn("fictional-", json.dumps(transport.requests))

        with patch.dict(os.environ, {"PROSPECT_TEST_SECRET": "fictional-script-state"}):
            transport, _ = self.transport(
                [robots(JAZZ_HOST), (JAZZ["url"], body(fixture, "text/html"))],
                {(JAZZ_HOST, "/apply")}, JAZZ,
            )
            with self.assertRaisesRegex(FetchError, pilot.PRIVATE):
                transport.get(JAZZ["url"])
            self.assertEqual(transport.global_stop_reason(), pilot.PRIVATE)

    def test_denies_other_tenants_private_endpoints_unknown_queries_and_missing_terms(self):
        t, wire = self.transport(terms={(GH_HOST, JOBS)})
        for url in (
            address(GH_HOST, "/v1/boards/another/jobs"), address(GH_HOST, JOBS + "/12"),
            address(GH_HOST, JOBS + "?token=private"), address("private.example", JOBS),
            "https://user:pass@boards-api.greenhouse.io" + JOBS,
            address(GH_HOST, "/v1/boards/example/jobs/%2e%2e"), address("app.jazz.co", "/feeds/google/xml/0"),
        ):
            with self.subTest(url=url), self.assertRaisesRegex(FetchError, "unsafe_endpoint"):
                t.get(url)
        with self.assertRaisesRegex(FetchError, pilot.POLICY):
            t.get(address(GH_HOST, META))
        self.assertEqual(wire.calls, [])

    def test_robots_allow_deny_unsupported_and_unavailable(self):
        for document, reason in (
            ("User-agent: *\nDisallow: /v1/\n", "robots_disallowed"),
            ("User-agent: *\nCrawl-delay: 400\n", "stricter_crawl_delay"),
            ("User-agent: *\nRequest-rate: unknown\n", "robots_unsupported"),
        ):
            with self.subTest(document=document):
                t, wire = self.transport([robots(GH_HOST, document)], {(GH_HOST, META)})
                with self.assertRaisesRegex(FetchError, reason):
                    t.get(address(GH_HOST, META))
                self.assertEqual(len(wire.calls), 1)
        robots_url = address(GH_HOST, "/robots.txt")
        t, wire = self.transport([(robots_url, pilot.Reply(503))], {(GH_HOST, META)})
        with self.assertRaisesRegex(FetchError, "http_503"):
            t.get(address(GH_HOST, META))
        with self.assertRaisesRegex(FetchError, "http_503"):
            t.get(address(GH_HOST, META))
        self.assertEqual(wire.calls, [robots_url])
        self.assertEqual(t.access[-1]["decision"], "robots_unavailable")

    def test_attempts_pacing_redirect_and_failure(self):
        original = address(JAZZ_HOST, "/apply")
        target = original + "/"
        t, wire = self.transport([robots(JAZZ_HOST), (original, pilot.Reply(302, location=target)),
                                  (target, None)], {(JAZZ_HOST, "/apply")}, JAZZ)
        with self.assertRaisesRegex(FetchError, "network_error"):
            t.get(original)
        self.assertEqual(len(wire.calls), 3)
        self.assertEqual(t.budget.counts["seeded"], 3)
        self.assertEqual([e["elapsed"] for e in t.requests], [0, 2, 4])
        self.assertEqual([e["redirect"] for e in t.requests], [False, False, True])
        with self.assertRaisesRegex(FetchError, "duplicate_request"):
            t.get(original)
        self.assertEqual(len(wire.calls), 3)

    def test_redirect_requires_same_host_route_and_current_robots(self):
        original = address(JAZZ_HOST, "/apply")
        for location in (original + "?signature=secret", "https://another.applytojob.com/apply",
                         original + "/AbC01234/Analyst", original + "/login"):
            t, wire = self.transport([robots(JAZZ_HOST), (original, pilot.Reply(302, location=location))],
                                     {(JAZZ_HOST, "/apply")}, JAZZ)
            reason = pilot.CHALLENGE if location.endswith("/login") else "unsafe_redirect"
            with self.assertRaisesRegex(FetchError, reason):
                t.get(original)
            self.assertEqual(len(wire.calls), 2)
            self.assertNotIn(location, json.dumps(t.requests))
        t, wire = self.transport([robots(JAZZ_HOST, "User-agent: *\nDisallow: /apply/\n"),
                                  (original, pilot.Reply(302, location=original + "/"))],
                                 {(JAZZ_HOST, "/apply")}, JAZZ)
        with self.assertRaisesRegex(FetchError, "robots_disallowed"):
            t.get(original)
        self.assertEqual(len(wire.calls), 2)

    def test_denials_and_challenge_stop_shared_host(self):
        for reply in (pilot.Reply(401), pilot.Reply(403), pilot.Reply(429, retry_after="3600"),
                      body("<html><title>Just a moment</title></html>", "text/html")):
            t, wire = self.transport([robots(GH_HOST), (address(GH_HOST, META), reply)],
                                     {(GH_HOST, META)})
            with self.assertRaises(FetchError):
                t.get(address(GH_HOST, META))
            t.stage_for_board("cold", GREEN)
            with self.assertRaisesRegex(FetchError, "host_stopped"):
                t.get(address(GH_HOST, META))
            self.assertEqual(len(wire.calls), 2)
            if reply.status == 429:
                self.assertEqual(t.requests[-1]["retry_after_seconds"], 3600)

    def test_byte_connect_read_and_whole_request_limits(self):
        url = address(GH_HOST, META)
        for reply, reason, byte_cap in (
            (pilot.Reply(connect=6), "request_timeout", 10000),
            (pilot.Reply(chunks=((11, b"{}"),)), "request_timeout", 10000),
            (pilot.Reply(chunks=((9, b"{"), (9, b" "), (9, b"}"))), "request_timeout", 10000),
            (body({"name": "many bytes"}), "byte_budget", 40),
        ):
            t, wire = self.transport([robots(GH_HOST), (url, reply)], {(GH_HOST, META)})
            with patch.object(pilot, "BYTE_LIMIT", byte_cap), self.assertRaisesRegex(FetchError, reason):
                t.get(url)
            self.assertEqual(len(wire.calls), 2)
        t, wire = self.transport([], {(GH_HOST, META)})
        t.deadline = 0
        with self.assertRaisesRegex(FetchError, "time_budget"):
            t.get(url)
        self.assertEqual(wire.calls, [])

    def test_private_response_halts_before_capture_and_sanitizes_ledger(self):
        url = address(GH_HOST, META)
        for payload in ({"sessionToken": "fixture-private"}, {"name": "fixture-private"}):
            with patch.dict(os.environ, {"TEST_TOKEN": "fixture-private"}):
                t, wire = self.transport([robots(GH_HOST), (url, body(payload))], {(GH_HOST, META)})
                with self.assertRaisesRegex(FetchError, pilot.PRIVATE):
                    t.get(url, capture=lambda _: self.fail("Unexpected capture"))
                self.assertEqual(t.halted, pilot.PRIVATE)
                self.assertNotIn("fixture-private", json.dumps(t.requests))
                self.assertNotIn("sha256", t.requests[-1])
                self.assertEqual(len(wire.calls), 2)

    def test_jazz_detail_requires_observed_route_and_cold_details_disabled(self):
        terms = {(JAZZ_HOST, "/apply"), (JAZZ_HOST, "/apply/{posting}")}
        original = address(JAZZ_HOST, "/apply")
        known = original + "/AbC01234/Program-Associate"
        page = body(f'<li class="list-group-item"><a href="{known}">Program Associate</a></li>', "text/html")
        t, wire = self.transport([robots(JAZZ_HOST), (original, page),
                                  (known, body("<title>Posting</title>", "text/html"))], terms, JAZZ)
        with self.assertRaisesRegex(FetchError, "unsafe_endpoint"):
            t.get(known)
        t.get(original)
        t.get(known)
        with self.assertRaisesRegex(FetchError, "unsafe_endpoint"):
            t.get(known)
        t.stage_for_board("cold", JAZZ, 1)
        with self.assertRaisesRegex(FetchError, "unsafe_endpoint"):
            t.get(known)
        self.assertEqual(len(wire.calls), 3)

    def test_sitemap_requires_explicit_advertisement(self):
        host = pilot.FEED_HOST
        path = "/feeds/google/xml/0"
        t, wire = self.transport([robots(host)], {(host, path)}, board=None)
        with self.assertRaisesRegex(FetchError, "sitemap_not_advertised"):
            t.get(address(host, path), prefix=True)
        self.assertEqual(len(wire.calls), 1)

    def test_jazz_nonempty_poll_uses_guarded_index_receipt(self):
        index = body((FIXTURES / "index.html").read_text(), "text/html")
        t, _ = self.transport([robots(JAZZ_HOST), (JAZZ["url"], index)],
                              {(JAZZ_HOST, "/apply")}, board=None)
        with tempfile.TemporaryDirectory() as scratch:
            _, report = pilot.run_fixture(Path(scratch) / "data", [JAZZ], t,
                                          code_sha=SHA, config_sha="b" * 64)
        poll = report["polls"][0]
        self.assertGreater(poll["received"], 0)
        self.assertEqual(poll["inventory_receipts"][0]["raw_sha"], poll["raw_sha"])

    def test_discovery_route_fixture_yields_cold_poll(self):
        host = pilot.INDEX_HOST
        index_path = "/CC-MAIN-2026-44-index"
        green_params = {"url": "job-boards.greenhouse.io/*", "output": "json", "filter": "status:200",
                        "fl": "url,timestamp,status", "limit": 2500, "pageSize": 1}
        feed_host = pilot.FEED_HOST
        feed_path = "/feeds/google/xml/0"
        feed_url = address(feed_host, feed_path)
        index = address(host, index_path)
        seed_robots = "User-agent: *\nDisallow:\nSitemap: " + feed_url + "\n"
        script = [
            robots(host),
            (address(host, "/collinfo.json"), body([{"id": "CC-MAIN-2026-44"}])),
            (index + "?" + urlencode(dict(green_params, showNumPages="true")), body({"pages": 1})),
            (index + "?" + urlencode(dict(green_params, page=0)),
             body(json.dumps({"url": GREEN["url"], "timestamp": "20261101000000"}) + "\n", "application/x-ndjson")),
            (index + "?" + urlencode(dict(green_params, url="jobs.lever.co/*", showNumPages="true")), body({"pages": 1})),
            (index + "?" + urlencode(dict(green_params, url="jobs.lever.co/*", page=0)), body("", "application/x-ndjson")),
            robots(feed_host, seed_robots),
            (feed_url, body("<urlset></urlset>", "text/xml")),
            robots(GH_HOST),
            (address(GH_HOST, JOBS, {"content": "true"}), body({"jobs": [], "meta": {"total": 0}})),
            (address(GH_HOST, META), body({"name": "Example"})),
        ]
        terms = {(host, "/collinfo.json"), (host, index_path), (feed_host, feed_path),
                 (GH_HOST, JOBS), (GH_HOST, META)}
        t, wire = self.transport(script, terms, board=None)
        with tempfile.TemporaryDirectory() as scratch:
            _, report = pilot.run_fixture(Path(scratch) / "data", [], t, code_sha=SHA, config_sha="b" * 64)
        self.assertEqual(report["stage_attempts"], {"discovery": 8, "cold": 3})
        self.assertEqual([p["status"] for p in report["polls"]], ["ok"])
        self.assertEqual(report["polls"][0]["arm"], "cold")
        self.assertEqual(report["candidates"][0]["evidence"][0]["source"], index)
        self.assertEqual(len(wire.calls), len(script))

    def test_interruption_preserves_evidence_and_refuses_unfinished_export(self):
        script = [robots(GH_HOST),
                  (address(GH_HOST, JOBS, {"content": "true"}), body({"jobs": []})),
                  (address(GH_HOST, META), KeyboardInterrupt())]
        t, _ = self.transport(script, {(GH_HOST, JOBS), (GH_HOST, META)}, board=None)
        with tempfile.TemporaryDirectory() as scratch:
            data = Path(scratch) / "data"
            with self.assertRaises(KeyboardInterrupt):
                pilot.run_fixture(data, [GREEN], t, code_sha=SHA, config_sha="b" * 64)
            self.assertTrue((data / "run" / "cohort.csv").is_file())
            self.assertFalse((data / "run" / "run-manifest.json").exists())
            with Store.in_data_dir(data, create=False) as store:
                self.assertIsNone(store.connection.execute("SELECT finished_at FROM run").fetchone()[0])
                self.assertEqual(store.connection.execute("SELECT status FROM poll").fetchone()[0], "failed")
                with self.assertRaisesRegex(ValueError, "unfinished run"):
                    export_snapshot(store, data / "exports", code_sha=SHA)

    def test_cold_selection_is_deterministic_bounded_and_excludes_seeds(self):
        entries = [(GREEN["url"], "dc_dmv")]
        for stem in ("test-a", "test-b", "test-c", "test-d"):
            entries += [(f"https://job-boards.greenhouse.io/{stem}", "dc_dmv" if stem == "test-c" else "unknown"),
                        (f"https://jobs.lever.co/{stem}", "unknown"),
                        (f"https://{stem}.applytojob.com/apply", "unknown")]
        results = []
        for order in (entries, reversed(entries)):
            registry = empty_registry()
            for url, hint in order:
                kind = "public_provider_sitemap" if ".applytojob.com" in url else "commoncrawl_url_index"
                source = ("https://app.jazz.co/feeds/google/xml/0" if kind == "public_provider_sitemap"
                          else "https://index.commoncrawl.org/CC-MAIN-2026-44-index")
                register(registry, url, dict(kind=kind, url=source, geography_hint=hint))
            results.append(pilot.select_cold(registry, [GREEN]))
        self.assertEqual(results[0], results[1])
        boards, ledger = results[0]
        self.assertEqual(len(boards), 6)
        self.assertEqual(boards[0]["key"], "greenhouse:test-c")
        self.assertEqual(sum(r["seed_overlap"] for r in ledger), 1)
        self.assertNotIn(GREEN, boards)

    def test_sitemap_query_hints_keep_only_public_projection_and_count_rejections(self):
        """Previously every public ?utm_source feed URL silently disappeared."""
        from career_engine.public_sources import discover_jazz_sitemap

        host = "example-policy.applytojob.com"
        posting = f"https://{host}/apply/AbC01234/Policy-Analyst"
        source = "https://app.jazz.co/feeds/google/xml/0"
        feed = (
            "<urlset>"
            f"<url><loc>{posting}?utm_source=google&amp;utm_medium=organic</loc></url>"
            f"<url><loc>{posting}?access_token=fixture-secret</loc></url>"
            f"<url><loc>{posting}?unknown_parameter=abc</loc></url>"
            f"<url><loc>{posting}/extra</loc></url>"
            "</urlset>"
        )

        class FakeFeed:
            checks_robots = True
            def get(self, url, **kwargs):
                assert url == source
                return feed

        registry = empty_registry()
        logs = discover_jazz_sitemap(FakeFeed(), registry, page_budget=1)
        self.assertEqual(logs[0]["status"], "ok")
        self.assertEqual(logs[0]["new_boards"], 1)
        self.assertEqual(logs[0]["complete_URL_entries"], 4)
        aggregate = {}
        selected, candidates = pilot.select_cold(registry, [], screening=aggregate)
        self.assertEqual([board["key"] for board in selected], ["jazzhr:example-policy"])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(aggregate["registered_boards"], 1)
        self.assertEqual(aggregate["evidence_accepted"], 1)
        self.assertEqual(aggregate["observation_rejected"], 1)
        self.assertEqual(aggregate["observation_query_rejected"], 1)
        self.assertEqual(aggregate["observation_route_rejected"], 1)
        self.assertEqual(aggregate["cold_candidates"], 1)
        self.assertEqual(aggregate["selected_boards"], 1)
        self.assertEqual(candidates[0]["evidence"][0]["observed_url"], posting)
        self.assertNotIn("utm_source", json.dumps(candidates))
        self.assertNotIn("fixture-secret", json.dumps(candidates))
        self.assertNotIn("unknown_parameter", json.dumps(candidates))
        self.assertNotIn(host, json.dumps(aggregate))

    def test_cold_public_board_tracking_hint_is_allowed_but_unknown_query_is_not(self):
        base = "https://test-policy.applytojob.com/apply"
        source = "https://app.jazz.co/feeds/google/xml/0"
        registry = empty_registry()
        for observed in (base + "?source=GoogleJobs", base + "?applicant=abc"):
            register(registry, observed, dict(kind="public_provider_sitemap",
                                              url=source, observed_url=observed))
        counts = {}
        selected, candidates = pilot.select_cold(registry, [], screening=counts)
        self.assertEqual([b["url"] for b in selected], [base])
        self.assertEqual(candidates[0]["evidence"][0]["observed_url"], base)
        self.assertEqual(counts["observation_query_rejected"], 1)
        self.assertEqual(counts["evidence_accepted"], 1)

    def test_partial_store_export_and_offline_reproduction(self):
        listing = dict(id="01234567-89ab-cdef-0123-456789abcdef", text="Analyst",
                       hostedUrl="https://jobs.lever.co/example/01234567-89ab-cdef-0123-456789abcdef",
                       categories={"location": "Washington, DC"})
        params2 = dict(LEV_QUERY, skip=100)
        jazz_index = (FIXTURES / "empty.html").read_text()
        script = [robots(LEVER_HOST), (address(LEVER_HOST, LEV_PATH, LEV_QUERY), body([listing] * 100)),
                  (address(LEVER_HOST, LEV_PATH, params2), None), robots(JAZZ_HOST),
                  (JAZZ["url"], body(jazz_index, "text/html"))]
        terms = {(LEVER_HOST, LEV_PATH), ("jobs.lever.co", "/example"), (JAZZ_HOST, "/apply")}
        t, _ = self.transport(script, terms, board=None)
        with tempfile.TemporaryDirectory() as scratch:
            snapshot, report = pilot.run_fixture(Path(scratch) / "data", [LEVER, JAZZ], t,
                                                  code_sha=SHA, config_sha="b" * 64)
            self.assertEqual([row["status"] for row in report["polls"]], ["partial", "ok"])
            self.assertEqual(report["polls"][0]["received"], 100)
            self.assertEqual(report["polls"][0]["unrepresented"], 0)
            self.assertEqual(report["polls"][1]["received"], 0)
            self.assertEqual(len((snapshot / "sightings.jsonl").read_text().splitlines()), 100)
            self.assertEqual(len((snapshot / "postings.jsonl").read_text().splitlines()), 1)
            manifest = json.loads((snapshot / "manifest.json").read_text())
            with Store.in_data_dir(Path(scratch) / "data", create=False) as store:
                reproduced = export_snapshot(store, Path(scratch) / "reproduced", code_sha=SHA,
                                             snapshot_id=snapshot.name, created_at=manifest["created_at"],
                                             window_from=manifest["window"]["from"], window_to=manifest["window"]["to"])
            for file in snapshot.iterdir():
                self.assertEqual(file.read_bytes(), (reproduced / file.name).read_bytes())
            if evaluator := os.getenv("PROSPECT_EVAL_CHECKOUT"):
                result = subprocess.run([sys.executable, "-m", "contract.validate", str(snapshot), "--json"],
                                        cwd=evaluator, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue(json.loads(result.stdout)["ok"])
