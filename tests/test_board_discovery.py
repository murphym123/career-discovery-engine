from collections import Counter
from datetime import datetime, timezone
from email.utils import formatdate
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from career_engine.ats_registry import empty_registry, register
from career_engine.board_discovery import DELAYS, Discovery, Limits, ROBOTS_TTL
from career_engine.board_discovery_http import RequestsTransport, Response
from career_engine.board_discovery_state import DiscoveryState
from career_engine.board_indexes import Plan, candidate
from career_engine.registry_state import read_registry, save_registry
from career_engine.store import Store
from tools.discover_boards import main

FIXTURES = Path(__file__).parent / "fixtures" / "board_discovery"
PLAN = Plan(datetime(2026, 1, 5, tzinfo=timezone.utc))


def wire(value=b"", status=200, **headers):
    body = value if isinstance(value, bytes) else json.dumps(value).encode()
    return Response(status, {name.replace("_", "-"): value for name, value in headers.items()}, body, len(body))


def cdx(*urls):
    return wire([["original", "timestamp"], *[[url, "20260101120000"] for url in urls]])


def reached():
    return wire(content_type="application/json")


class FixtureTransport:
    def __init__(self, handler, clock):
        self.handler, self.clock = handler, clock
        self.calls = []

    def request(self, method, url, byte_limit, seconds):
        parsed = urlsplit(url)
        params = parse_qs(parsed.query)
        self.calls.append((method, parsed.hostname, parsed.path, params, self.clock()))
        return self.handler(method, parsed, params)


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        self.store = Store.in_data_dir(self.path)
        self.addCleanup(lambda: self.store.connection.close())
        self.state = DiscoveryState(self.store)
        self.now = PLAN.through.timestamp()

    def sleep(self, seconds):
        self.now += seconds

    def run_discovery(self, handler, *, campaign="test", plan=PLAN, mode="both", prefixes=(), **limits):
        transport = FixtureTransport(handler, lambda: self.now)
        settings = {"requests": 100, "pages": 40, "concurrency": 1,
                    "origin_delays": dict.fromkeys(DELAYS, 0), **limits}
        result = Discovery(self.state, transport, campaign, plan, Limits(**settings), mode=mode,
                           denied_prefixes=prefixes, clock=lambda: self.now, monotonic=lambda: self.now,
                           sleep=self.sleep).run()
        return result, transport.calls

    def reopen(self):
        self.store.connection.close()
        self.store = Store.in_data_dir(self.path, create=False)
        self.state = DiscoveryState(self.store)

    def test_additive_archive_and_recent_evidence_then_publish(self):
        def handler(method, url, params):
            if url.path == "/robots.txt":
                return wire(status=404)
            if url.hostname == "web.archive.org":
                domain = params["url"][0]
                self.assertEqual(params["matchType"], ["host"])
                self.assertEqual(params["to"], ["20260105000000"])
                if domain == "boards.greenhouse.io":
                    follow = params.get("resumeKey")
                    if follow:
                        self.assertEqual(follow, ["io,greenhouse,boards)/synthetic 20260101120005!"])
                    file = "wayback-second.json" if follow else "wayback-first.json"
                    return wire((FIXTURES / file).read_bytes())
                if domain == "job-boards.greenhouse.io":
                    return cdx(f"https://{domain}/Synthetic")
                if domain in {"jobs.lever.co", "jobs.eu.lever.co"}:
                    return cdx(f"https://{domain}/synthetic")
                return cdx(f"https://{domain}/name%20with%20spaces", f"https://{domain}/root.12345")
            if url.hostname == "urlscan.io":
                query = params["q"][0]
                self.assertIn("visibility:public", query)
                if "job-boards.greenhouse.io" in query:
                    if "search_after" not in params:
                        return wire((FIXTURES / "urlscan-first.json").read_bytes())
                    self.assertEqual(params["search_after"], ["1767438000000,11337701-1234-1234-1234-123456789012"])
                    return wire({"results": [{"_id": "11337702-1234-1234-1234-123456789012",
                                              "task": {"visibility": "public", "time": "2026-01-03T12:00:00Z"},
                                              "page": {"url": "https://job-boards.greenhouse.io/recent-only"}}],
                                 "has_more": False})
                return wire({"results": [], "has_more": False})
            self.assertEqual(method, "HEAD")
            return reached()

        result, calls = self.run_discovery(handler)
        self.assertEqual(result["boards"]["validated"], 6)
        self.assertEqual(result["boards"]["candidate"], 0)
        self.assertEqual(result["evidence"], 9)
        probes = [(host, path) for method, host, path, _, _ in calls if method == "HEAD"]
        self.assertEqual(Counter(host for host, _ in probes), {"boards-api.greenhouse.io": 3, "api.lever.co": 1,
                         "api.eu.lever.co": 1, "api.ashbyhq.com": 1})
        self.assertIn(("api.ashbyhq.com", "/posting-api/job-board/name%20with%20spaces"), probes)
        evidence = list(self.state.db.execute("SELECT observed_url FROM board_discovery_evidence ORDER BY id"))
        self.assertIn("http://boards.greenhouse.io/Synthetic/jobs/123", [row[0] for row in evidence])
        self.assertTrue(all("?" not in row[0] and "Unlisted" not in row[0] for row in evidence))
        self.assertEqual(sum(stream["rejected"] for stream in result["streams"]), 6)
        rerun, calls = self.run_discovery(lambda *_: self.fail("completed campaign made a request"))
        self.assertEqual(rerun["evidence"], 9)
        self.assertEqual(calls, [])

        registry = empty_registry()
        prior = {"kind": "source_audit_link", "url": "https://example.invalid/jobs"}
        register(registry, "https://boards.greenhouse.io/synthetic", prior)
        registry["boards"]["greenhouse:synthetic"].update(last_checked_run=4, status="partial_failure")
        path = self.path / "ats-registry.json"
        save_registry(path, registry)
        self.assertEqual(self.state.publish(path), {"added": 5, "updated": 1, "registry_boards": 6})
        entry = read_registry(path)["boards"]["greenhouse:synthetic"]
        self.assertEqual(entry["last_checked_run"], 4)
        self.assertEqual(entry["status"], "partial_failure")
        self.assertIn(prior, entry["evidence"])
        self.assertEqual(entry["discovery_verification"]["method"], "head_reachable")
        prior_bytes = path.read_bytes()
        self.assertEqual(self.state.publish(path)["updated"], 0)
        self.assertEqual(path.read_bytes(), prior_bytes)

    def test_page_failure_interruption_and_resume(self):
        follow_attempts = 0

        def handler(method, url, params):
            nonlocal follow_attempts
            if url.path == "/robots.txt":
                return wire(status=404)
            if params["url"] != ["boards.greenhouse.io"]:
                return cdx()
            if "resumeKey" not in params:
                return wire((FIXTURES / "wayback-first.json").read_bytes())
            follow_attempts += 1
            if follow_attempts == 1:
                return wire(b"malformed JSON")
            if follow_attempts == 2:
                raise KeyboardInterrupt()
            return wire((FIXTURES / "wayback-second.json").read_bytes())

        plan = Plan(PLAN.through, recent_days=0)
        first, _ = self.run_discovery(handler, plan=plan, mode="index")
        target = next(row for row in first["streams"] if row["host"] == "boards.greenhouse.io")
        self.assertEqual((target["status"], target["pages"]), ("failed", 1))
        self.assertEqual(first["evidence"], 2)
        self.reopen()
        stopped, _ = self.run_discovery(handler, plan=plan, mode="index")
        self.assertEqual(stopped["stop_reason"], "interrupted")
        self.reopen()
        complete, calls = self.run_discovery(handler, plan=plan, mode="index")
        self.assertEqual(complete["evidence"], 3)
        self.assertEqual(complete["boards"]["candidate"], 2)
        self.assertEqual(len(calls), 1)
        attempts = list(self.state.db.execute("""
            SELECT a.cursor_digest FROM board_discovery_attempt a JOIN board_discovery_stream s ON a.stream=s.id
            WHERE s.host='boards.greenhouse.io' ORDER BY a.id
        """))
        self.assertNotEqual(attempts[0][0], attempts[1][0])
        self.assertEqual(attempts[1][0], attempts[2][0])
        with self.assertRaisesRegex(ValueError, "Campaign index parameters changed"):
            self.run_discovery(handler, plan=Plan(PLAN.through, page_size=10))

    def test_commoncrawl_fallback_is_per_host_and_honors_robots(self):
        def handler(method, url, params):
            if url.path == "/robots.txt":
                if url.hostname == "index.commoncrawl.org":
                    return wire(b"User-agent: *\nDisallow: /\nAllow: /collinfo.json$\n")
                return wire(status=404)
            if url.hostname == "web.archive.org":
                if params["url"] == ["jobs.ashbyhq.com"]:
                    return wire(status=502)
                return cdx("https://" + params["url"][0] + "/synthetic")
            self.fail(f"unexpected request: {url}")

        plan = Plan(PLAN.through, recent_days=0, commoncrawl="CC-MAIN-2026-40")
        result, calls = self.run_discovery(handler, plan=plan, mode="index")
        self.assertEqual([path for _, host, path, _, _ in calls if host == "index.commoncrawl.org"], ["/robots.txt"])
        fallback = {entry["host"]: entry for entry in result["streams"] if entry["source"] == "commoncrawl"}
        self.assertEqual(fallback["jobs.ashbyhq.com"]["status"], "denied")
        self.assertIn("disallow: /", fallback["jobs.ashbyhq.com"]["reason"])
        self.assertEqual(fallback["jobs.eu.lever.co"]["status"], "standby")

    def test_eligible_commoncrawl_walks_pages_and_preserves_wayback_failure(self):
        def handler(method, url, params):
            if url.path == "/robots.txt":
                return wire(status=404)
            if url.hostname == "web.archive.org":
                if params["url"] == ["jobs.ashbyhq.com"]:
                    return wire(status=502)
                return cdx("https://" + params["url"][0] + "/synthetic")
            self.assertEqual(params["url"], ["jobs.ashbyhq.com/*"])
            self.assertEqual(url.path, "/CC-MAIN-2026-40-index")
            if "showNumPages" in params:
                return wire({"pages": 3})
            if params["page"] == ["1"]:
                return wire(b"")  # a zero-result page within the server's range
            return wire(b'{"url":"https://jobs.ashbyhq.com/fallback", "timestamp":"20260101120000"}\n')

        plan = Plan(PLAN.through, recent_days=0, commoncrawl="CC-MAIN-2026-40")
        result, calls = self.run_discovery(handler, plan=plan, mode="index")
        fallback = [q["page"][0] for _, h, _, q, _ in calls if h == "index.commoncrawl.org" and "page" in q]
        self.assertEqual(fallback, ["0", "1", "2"])
        rows = {(r["source"], r["host"]): r for r in result["streams"]}
        self.assertEqual(rows[("wayback", "jobs.ashbyhq.com")]["status"], "failed")
        self.assertEqual(rows[("commoncrawl", "jobs.ashbyhq.com")]["status"], "complete")
        self.assertEqual(result["boards"]["candidate"], 4)

    def test_head_fallback_robots_404_challenge_and_case_variant(self):
        def handler(method, url, params):
            if url.path == "/robots.txt":
                if url.hostname == "api.lever.co":
                    return wire(b"User-agent: *\nDisallow: /v0/postings/robots-denied\n")
                return wire(status=404)
            if url.hostname == "web.archive.org":
                host = params["url"][0]
                slugs = {"jobs.lever.co": ("robots-denied", "Missing", "missing", "no-board", "fallback", "bad-schema"),
                         "jobs.ashbyhq.com": ("challenge", "never-probed"), "jobs.eu.lever.co": ("fallback",)}
                return cdx(*(f"https://{host}/{name}" for name in slugs.get(host, ())))
            slug = url.path.rsplit("/", 1)[1]
            if slug in {"Missing", "no-board"}:
                return wire(status=404)
            if slug in {"fallback", "bad-schema"}:
                if method == "HEAD":
                    return wire(status=405)
                return wire([] if slug == "fallback" else {"error": "not a board"})
            if slug == "challenge":
                return wire(status=403)
            self.assertEqual(slug, "missing")
            return reached()

        result, calls = self.run_discovery(handler, plan=Plan(PLAN.through, recent_days=0))
        self.assertEqual(result["boards"], {"validated": 3, "candidate": 0, "failed": 1, "missing": 1,
                                            "denied": 3, "deferred": 0})
        self.assertFalse(any(path.endswith(("robots-denied", "never-probed")) for _, _, path, _, _ in calls))
        observations = dict(self.state.db.execute("SELECT url,verification FROM board_discovery_variant v JOIN board_discovery_board b USING (board_key) WHERE v.status='validated'"))
        self.assertEqual(observations["https://jobs.lever.co/missing"], "head_reachable")
        self.assertEqual(observations["https://jobs.eu.lever.co/fallback"], "get_board_schema")
        self.reopen()
        only_failures, calls = self.run_discovery(handler, plan=Plan(PLAN.through, recent_days=0))
        self.assertEqual(only_failures["boards"], result["boards"])
        self.assertEqual([(method, path) for method, _, path, _, _ in calls], [("GET", "/v0/postings/bad-schema")])

    def test_budget_can_resume_one_request_at_a_time_and_robots_expires(self):
        plan = Plan(PLAN.through, recent_days=0)

        def handler(method, url, params):
            if url.path == "/robots.txt":
                return wire(status=404)
            return cdx()

        first, calls = self.run_discovery(handler, plan=plan, mode="index", requests=1)
        self.assertEqual(first["stop_reason"], "request_budget")
        self.assertEqual(calls[0][2], "/robots.txt")
        self.reopen()
        second, calls = self.run_discovery(handler, plan=plan, mode="index", requests=1)
        self.assertEqual(second["page_requests"], 1)
        self.assertEqual(calls[0][3]["url"], ["job-boards.greenhouse.io"])
        self.sleep(ROBOTS_TTL + 1)
        _, calls = self.run_discovery(handler, plan=plan, mode="index", requests=1)
        self.assertEqual(calls[0][2], "/robots.txt")

    def test_page_and_byte_budgets_leave_pending_streams_fair_on_resume(self):
        plan = Plan(PLAN.through, recent_days=0)

        def handler(method, url, params):
            if url.path == "/robots.txt":
                return wire(status=404)
            return cdx(f"https://{params['url'][0]}/left-pending")

        result, calls = self.run_discovery(handler, plan=plan, mode="index", bytes=200, response_bytes=200)
        self.assertEqual(result["stop_reason"], "byte_budget")
        self.assertEqual(result["page_requests"], 1)
        self.assertEqual(calls[-1][3]["url"], ["job-boards.greenhouse.io"])
        self.reopen()
        result, calls = self.run_discovery(handler, plan=plan, mode="index", pages=1)
        self.assertEqual(result["stop_reason"], "page_budget")
        self.assertEqual(calls[0][3]["url"], ["boards.greenhouse.io"])
        self.assertEqual(result["boards"]["candidate"], 1)
        self.assertEqual(result["evidence"], 2)

    def test_repeated_resume_cursor_keeps_observation_and_checkpoint(self):
        plan = Plan(PLAN.through, recent_days=0)

        def handler(method, url, params):
            if url.path == "/robots.txt":
                return wire(status=404)
            host = params["url"][0]
            if host != "jobs.lever.co":
                return cdx()
            slug = "second" if "resumeKey" in params else "first"
            return wire([["original", "timestamp"], [f"https://{host}/{slug}", "20260101000000"], [], ["cursor"]])

        result, _ = self.run_discovery(handler, plan=plan, mode="index")
        lever = next(row for row in result["streams"] if row["host"] == "jobs.lever.co")
        self.assertEqual((lever["status"], lever["reason"], lever["pages"]), ("failed", "repeated_index_cursor", 1))
        self.assertEqual(result["evidence"], 2)
        self.reopen()
        result, calls = self.run_discovery(handler, plan=plan, mode="index")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][3]["resumeKey"], ["cursor"])
        self.assertEqual(result["evidence"], 2)

    def test_page_retries_do_not_starve_unvisited_streams_or_validation(self):
        plan = Plan(PLAN.through, recent_days=0)

        def handler(method, url, params):
            if url.path == "/robots.txt":
                return wire(status=404)
            if url.hostname == "web.archive.org":
                host = params["url"][0]
                if host == "job-boards.greenhouse.io":
                    return wire(status=502)
                return cdx(f"https://{host}/broken", f"https://{host}/healthy") if host == "jobs.lever.co" else cdx()
            return reached() if url.path.endswith("/healthy") else wire(status=500)

        for _ in range(6):
            self.run_discovery(handler, plan=plan, mode="index", requests=1)
        self.run_discovery(handler, plan=plan, mode="validate", requests=2)
        report, calls = self.run_discovery(handler, plan=plan, mode="validate", requests=1)
        self.assertEqual(calls[0][2], "/v0/postings/healthy")
        self.assertEqual(report["boards"]["validated"], 1)
        self.assertEqual(report["boards"]["failed"], 1)

    def test_operator_prefix_and_robots_pacing(self):
        plan = Plan(PLAN.through, recent_days=0)

        def handler(method, url, params):
            if url.path == "/robots.txt":
                return wire(b"User-agent: *\nCrawl-delay: 1\n")
            if url.hostname == "web.archive.org":
                host = params["url"][0]
                return cdx(*(f"https://{host}/{slug}" for slug in ("one", "two", "withheld"))) if host == "jobs.lever.co" else cdx()
            self.assertFalse(url.path.endswith("/withheld"))
            return reached()

        result, calls = self.run_discovery(handler, plan=plan, prefixes=("https://api.lever.co/v0/postings/withheld",))
        self.assertEqual(result["boards"]["denied"], 1)
        self.assertEqual(result["boards"]["validated"], 2)
        times = [started for _, host, _, _, started in calls if host == "api.lever.co"]
        self.assertEqual(len(times), 3)
        self.assertTrue(all(end-start >= 1 for start, end in zip(times, times[1:])))

    def test_retry_after_persists_and_other_origins_continue(self):
        plan = Plan(PLAN.through, recent_days=0)
        original = self.now

        def handler(method, url, params):
            if url.path == "/robots.txt":
                return wire(status=404)
            if url.hostname == "web.archive.org":
                host = params["url"][0]
                return cdx(f"https://{host}/a", f"https://{host}/second")
            if url.hostname == "api.lever.co" and self.now < original + 90:
                return wire(status=429, retry_after="90")
            if url.hostname == "api.eu.lever.co" and self.now < original + 120:
                return wire(status=503, retry_after=formatdate(original + 120, usegmt=True))
            return reached()

        first, calls = self.run_discovery(handler, plan=plan)
        self.assertEqual(first["boards"]["validated"], 4)
        self.assertEqual(first["boards"]["deferred"], 2)
        self.assertEqual(self.state.origin("api.lever.co")[0], original + 90)
        self.assertEqual(self.state.origin("api.eu.lever.co")[0], original + 120)
        for host in ("api.lever.co", "api.eu.lever.co"):
            self.assertEqual(sum(m == "HEAD" and h == host for m, h, *_ in calls), 1)
        self.reopen()
        waiting, calls = self.run_discovery(handler, plan=plan)
        self.assertEqual(waiting["stop_reason"], "origin_cooldown")
        self.assertEqual(calls, [])
        self.sleep(121)
        completed, calls = self.run_discovery(handler, plan=plan)
        self.assertEqual(completed["boards"]["validated"], 8)
        self.assertEqual(len(calls), 4)

    def test_concurrent_independent_origins_with_serial_requests_on_each(self):
        plan = Plan(PLAN.through, recent_days=0)

        def index(method, url, params):
            if url.path == "/robots.txt":
                return wire(status=404)
            return cdx(*(f"https://{params['url'][0]}/{slug}" for slug in ("one", "two")))

        self.run_discovery(index, plan=plan, mode="index")
        pending = threading.Barrier(4)
        lock = threading.Lock()
        active = set()

        def handler(method, url, params):
            if url.path == "/robots.txt":
                return wire(status=404)
            with lock:
                self.assertNotIn(url.hostname, active)
                active.add(url.hostname)
            pending.wait(timeout=5)
            with lock:
                active.remove(url.hostname)
            return reached()

        stats, _ = self.run_discovery(handler, plan=plan, mode="validate", concurrency=4)
        self.assertEqual(stats["boards"]["validated"], 8)

    def test_origin_slots_do_not_probe_aliases_in_parallel_or_start_after_challenge(self):
        plan = Plan(PLAN.through, recent_days=0)

        def index(method, url, params):
            if url.path == "/robots.txt":
                return wire(status=404)
            host = params["url"][0]
            slugs = ("One", "one", "two", "three") if host == "jobs.lever.co" else ()
            return cdx(*(f"https://{host}/{slug}" for slug in slugs))

        self.run_discovery(index, plan=plan, mode="index")
        pending = threading.Barrier(2)
        probed = set()
        lock = threading.Lock()
        challenge_recorded = threading.Event()
        set_origin = self.state.set_origin

        def track_stop(origin, not_before, stop):
            set_origin(origin, not_before, stop)
            if stop == "challenge":
                challenge_recorded.set()

        def handler(method, url, params):
            if url.path == "/robots.txt":
                return wire(status=404)
            slug = url.path.rsplit("/", 1)[1]
            with lock:
                self.assertIn(slug, {"One", "two"})
                probed.add(slug)
            pending.wait(timeout=5)
            if slug == "One":
                return wire(cf_mitigated="challenge")
            self.assertTrue(challenge_recorded.wait(timeout=5))
            return reached()

        with patch.object(self.state, "set_origin", side_effect=track_stop):
            result, _ = self.run_discovery(handler, plan=plan, mode="validate", concurrency=8,
                                           origin_concurrency={"api.lever.co": 2})
        self.assertEqual(probed, {"One", "two"})
        self.assertEqual(result["boards"]["validated"], 1)
        self.assertEqual(result["boards"]["denied"], 2)
        self.assertEqual(self.state.origin("api.lever.co")[1], "challenge")

    def test_publication_exceeds_legacy_seed_cap_and_cli_is_offline(self):
        plan = Plan(PLAN.through, recent_days=0, page_size=6_000)

        def handler(method, url, params):
            if url.path == "/robots.txt":
                return wire(status=404)
            if method == "HEAD":
                return reached()
            host = params["url"][0]
            return cdx(*(f"https://{host}/tenant-{i}" for i in range(5_050))) if host == "jobs.lever.co" else cdx()

        result, _ = self.run_discovery(handler, plan=plan, requests=6_000, concurrency=4)
        self.assertEqual(result["boards"]["validated"], 5_050)
        registry = self.path / "ats-registry.json"
        with patch("tools.discover_boards.RequestsTransport", side_effect=AssertionError("Unexpected transport")):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                self.assertEqual(main(["--data-dir", str(self.path), "publish", "--registry", str(registry)]), 0)
                self.assertEqual(json.loads(stdout.getvalue())["added"], 5_050)
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                self.assertEqual(main(["--data-dir", str(self.path), "status", "--campaign", "test"]), 0)
                self.assertEqual(json.loads(stdout.getvalue())["boards"]["validated"], 5_050)
        self.assertIn("lever:tenant-5049", read_registry(registry)["boards"])


class TransportTests(unittest.TestCase):
    def test_streaming_transport_never_follows_nor_drains_redirects(self):
        url = "https://api.lever.co/v0/postings/tenant?mode=json&limit=1"
        reply = Mock(status_code=302, headers={"Location": "https://other.invalid/"})
        reply.__enter__ = Mock(return_value=reply)
        reply.__exit__ = Mock()
        with RequestsTransport("ops@example.invalid") as transport:
            session = transport.sessions["api.lever.co"]
            with patch.object(session.get_adapter(url), "send", return_value=reply) as send:
                self.assertEqual(transport.request("GET", url, 50, 1).status, 302)
                send.assert_called_once()
                reply.raw.read.assert_not_called()

    def test_byte_cap_and_unexpected_hosts(self):
        url = "https://api.lever.co/v0/postings/tenant"
        reply = Mock(status_code=200, headers={})
        reply.__enter__ = Mock(return_value=reply)
        reply.__exit__ = Mock()
        reply.raw.read.side_effect = lambda amount, **kwargs: b"x" * amount
        with RequestsTransport("ops@example.invalid") as transport:
            session = transport.sessions["api.lever.co"]
            with patch.object(session.get_adapter(url), "send", return_value=reply):
                result = transport.request("GET", url, 60, 1)
        self.assertEqual((result.error, result.bytes_read), ("response_byte_limit", 60))
        reply.raw.read.assert_called_once_with(60, decode_content=False)
        with RequestsTransport("ops@example.invalid") as transport, self.assertRaises(ValueError):
            transport.request("GET", "https://api.lever.co@other.invalid/", 60, 1)

    def test_bad_observed_urls_do_not_escape_expected_host(self):
        host = "jobs.ashbyhq.com"
        for value in (None, f"https://{host}/%2fsuffix", f"https://{host}/%252f", f"https://{host}:999/bad",
                      f"https://{host}/root.aaa", f"https://{host}/bad%09name", f"https://{host}//name"):
            with self.subTest(value=value):
                self.assertIsNone(candidate(value, host))
        self.assertEqual(candidate(f"https://{host}/a%2Eb?never-copy=1", host)[0]["key"], "ashby:a.b")
