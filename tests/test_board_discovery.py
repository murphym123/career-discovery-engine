from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import formatdate
import fcntl
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from career_engine.ats_registry import empty_registry, register, validate_registry
from career_engine.board_discovery import DELAYS, Discovery, Limits, ROBOTS_TTL
from career_engine.board_discovery_http import RequestsTransport, Response
from career_engine.board_discovery_state import DiscoveryState, utc
from career_engine.board_indexes import Page, Plan, candidate
from career_engine.registry_state import read_registry, save_registry
from career_engine.store import Store
from tools.discover_boards import main
from tools.expand_ats import expand
from tools.pilot39_robots import RobotsRules
from test_ats import Session, transport as legacy_transport

FIXTURES = Path(__file__).parent / "fixtures" / "board_discovery"
PLAN = Plan(datetime(2026, 1, 5, tzinfo=timezone.utc))


def wire(value=b"", status=200, **headers):
    body = value if isinstance(value, bytes) else json.dumps(value).encode()
    return Response(status, {name.replace("_", "-"): value for name, value in headers.items()}, body, len(body))


def cdx(*urls):
    return wire([["original", "timestamp"], *[[url, "20260101120000"] for url in urls]])


def reached(method, url):
    if method == "HEAD":
        return wire(content_type="application/json")
    if url.hostname == "boards-api.greenhouse.io":
        return wire({"name": "Fixture board"})
    if url.hostname == "api.ashbyhq.com":
        return wire({"jobs": []})
    return wire([])


class FixtureTransport:
    def __init__(self, handler, clock):
        self.handler, self.clock = handler, clock
        self.calls = []

    def request(self, method, url, byte_limit, seconds, *, before_send):
        parsed = urlsplit(url)
        params = parse_qs(parsed.query)
        if before_send() <= 0:
            return Response(error="dispatch_window_expired")
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

    def run_discovery(self, handler, *, campaign="test", plan=PLAN, mode="both", prefixes=(),
                      denied_methods=(), replace_policy=False, **limits):
        transport = FixtureTransport(handler, lambda: self.now)
        settings = {"requests": 100, "pages": 40, "concurrency": 1,
                    "origin_delays": dict.fromkeys(DELAYS, 0), **limits}
        result = Discovery(self.state, transport, campaign, plan, Limits(**settings), mode=mode,
                           denied_prefixes=prefixes, denied_methods=denied_methods,
                           replace_access_policy=replace_policy,
                           clock=lambda: self.now, monotonic=lambda: self.now,
                           sleep=self.sleep).run()
        return result, transport.calls

    def reopen(self):
        self.store.connection.close()
        self.store = Store.in_data_dir(self.path, create=False)
        self.state = DiscoveryState(self.store)

    def index_lever(self, *tenants, **options):
        def handler(method, url, params):
            if url.path == "/robots.txt":
                return wire(status=404)
            host = params["url"][0]
            return cdx(*(f"https://{host}/{slug}" for slug in tenants)) if host == "jobs.lever.co" else cdx()

        return self.run_discovery(handler, mode="index", **options)

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
            return reached(method, url)

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
        self.assertEqual(entry["discovery_verification"]["method"], "get_board_schema")
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
        self.assertEqual(fallback["jobs.ashbyhq.com"]["reason"], "robots: disallow")
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
            return reached(method, url)

        result, calls = self.run_discovery(handler, plan=Plan(PLAN.through, recent_days=0))
        self.assertEqual(result["boards"], {"validated": 3, "candidate": 0, "failed": 1, "missing": 1,
                                            "denied": 3, "deferred": 0})
        self.assertFalse(any(path.endswith(("robots-denied", "never-probed")) for _, _, path, _, _ in calls))
        observations = dict(self.state.db.execute("SELECT url,verification FROM board_discovery_variant v JOIN board_discovery_board b USING (board_key) WHERE v.status='validated'"))
        self.assertEqual(observations["https://jobs.lever.co/missing"], "get_board_schema")
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
            cursor = "key+value%2B1" if slug == "first" else "key%20value%2B1"
            return wire([["original", "timestamp"], [f"https://{host}/{slug}", "20260101000000"], [], [cursor]])

        result, calls = self.run_discovery(handler, plan=plan, mode="index")
        self.assertEqual([q["resumeKey"] for _, _, _, q, _ in calls if "resumeKey" in q], [["key value+1"]])
        lever = next(row for row in result["streams"] if row["host"] == "jobs.lever.co")
        self.assertEqual((lever["status"], lever["reason"], lever["pages"]), ("failed", "repeated_index_cursor", 1))
        self.assertEqual(result["evidence"], 2)
        self.reopen()
        result, calls = self.run_discovery(handler, plan=plan, mode="index")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][3]["resumeKey"], ["key value+1"])
        self.assertEqual(result["evidence"], 2)

    def test_cursor_repeat_matches_earlier_wire_value_after_reopen(self):
        plan = Plan(PLAN.through, recent_days=0)
        turns = {None: "a+b", "a b": "c%2Bd", "c+d": "a%20b"}

        def handler(method, url, params):
            if url.path == "/robots.txt":
                return wire(status=404)
            if params["url"] != ["jobs.lever.co"]:
                return cdx()
            key = params.get("resumeKey", [None])[0]
            return wire([["original", "timestamp"], [], [turns[key]]])

        self.run_discovery(handler, plan=plan, mode="index", pages=6)
        self.reopen()
        result, calls = self.run_discovery(handler, plan=plan, mode="index")
        stream = next(row for row in result["streams"] if row["host"] == "jobs.lever.co")
        self.assertEqual((stream["pages"], stream["reason"]), (2, "repeated_index_cursor"))
        self.assertEqual([q["resumeKey"] for _, _, _, q, _ in calls], [["c+d"]])

    def test_commoncrawl_cutoff_and_oversized_count_are_stream_local(self):
        plan = Plan(PLAN.through, archive_from="20260101", recent_days=0, commoncrawl="CC-MAIN-2026-40")

        def handler(method, url, params):
            if url.path == "/robots.txt":
                return wire(status=404)
            if url.hostname == "web.archive.org":
                return wire(status=502) if params["url"][0] in ("job-boards.greenhouse.io", "jobs.lever.co") else cdx(
                    "https://" + params["url"][0] + "/another")
            if "showNumPages" in params:
                return wire({"pages": 2**63 if params["url"] == ["job-boards.greenhouse.io/*"] else 1})
            return wire(b"\n".join(json.dumps({"url": f"https://jobs.lever.co/{slug}", "timestamp": at}).encode()
                                   for slug, at in (("latest", "20260105000000"), ("future", "20260105000001"),
                                                    ("old", "20251231235959"), ("invalid", "20260104127300"),
                                                    ("undated", None))))

        result, _ = self.run_discovery(handler, plan=plan, mode="index")
        streams = {(row["source"], row["host"]): row for row in result["streams"]}
        broken = streams["commoncrawl", "job-boards.greenhouse.io"]
        self.assertEqual((broken["status"], broken["reason"]), ("failed", "commoncrawl_page_count"))
        good = streams["commoncrawl", "jobs.lever.co"]
        self.assertEqual((good["status"], good["rows"], good["rejected"], good["out_of_window"]),
                         ("complete", 5, 4, 2))
        self.assertEqual(list(map(tuple, self.state.db.execute("""
            SELECT board_key, source_time FROM board_discovery_evidence WHERE board_key LIKE 'lever:%'
        """))), [("lever:latest", "20260105000000")])
        self.assertEqual(streams["wayback", "jobs.ashbyhq.com"]["status"], "complete")

    def test_legacy_commoncrawl_observation_outside_cutoff_is_ineligible(self):
        plan = Plan(PLAN.through, recent_days=0, commoncrawl="CC-MAIN-2026-40")
        self.state.access_policy(())
        self.state.setup("test", plan)
        stream = next(row for row in self.state.streams("test") if row["source"] == "commoncrawl"
                      and row["host"] == "jobs.lever.co")
        self.state.record_page(stream, Page([
            ("https://jobs.lever.co/legacy", "20260101000000", ""),
        ], None), utc(self.now))
        self.state.db.execute("UPDATE board_discovery_evidence SET source_time='20261001000000'")
        self.state.db.execute("ALTER TABLE board_discovery_evidence DROP COLUMN eligible")
        self.reopen()
        result, calls = self.run_discovery(lambda *_: self.fail("probed ineligible evidence"), plan=plan, mode="validate")
        self.assertEqual(calls, [])
        self.assertEqual((result["evidence"], result["legacy_ineligible_evidence"]), (0, 1))
        variant = self.state.db.execute("SELECT * FROM board_discovery_variant").fetchone()
        self.state.set_variant(variant, "validated", "get_board_schema", "GET", utc(self.now))
        self.assertEqual(self.state.publish(self.path / "ats-registry.json")["registry_boards"], 0)

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
            return reached(method, url) if url.path.endswith("/healthy") else wire(status=500)

        for _ in range(6):
            self.run_discovery(handler, plan=plan, mode="index", requests=1)
        self.run_discovery(handler, plan=plan, mode="validate", requests=2)
        report, calls = self.run_discovery(handler, plan=plan, mode="validate", requests=1)
        self.assertEqual(calls[0][2], "/v0/postings/healthy")
        self.assertEqual(report["boards"]["deferred"], 1)
        report, calls = self.run_discovery(handler, plan=plan, mode="validate", requests=1)
        self.assertEqual((calls[0][0], calls[0][2]), ("GET", "/v0/postings/healthy"))
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
            return reached(method, url)

        result, calls = self.run_discovery(handler, plan=plan, prefixes=("https://api.lever.co/v0/postings/withheld",))
        self.assertEqual(result["boards"]["denied"], 1)
        self.assertEqual(result["boards"]["validated"], 2)
        times = [started for _, host, _, _, started in calls if host == "api.lever.co"]
        self.assertEqual(len(times), 5)
        self.assertTrue(all(end-start >= 1 for start, end in zip(times, times[1:])))

    def test_saved_policy_applies_to_fallback_get_and_other_campaigns(self):
        plan = Plan(PLAN.through, recent_days=0)
        entire = "https://api.lever.co/v0/postings/withheld"
        get_only = "https://api.lever.co/v0/postings/head%2Donly"
        self.index_lever("withheld", "head-only", plan=plan, prefixes=(entire,),
                         denied_methods=(("GET", get_only),))

        def handler(method, url, params):
            if url.path == "/robots.txt":
                return wire(status=404)
            return wire(status=405) if method == "HEAD" else reached(method, url)

        self.reopen()
        result, calls = self.run_discovery(handler, plan=plan, campaign="separate", mode="validate")
        self.assertEqual([(m, path) for m, _, path, _, _ in calls],
                         [("GET", "/robots.txt"), ("HEAD", "/v0/postings/head-only")])
        self.assertEqual(result["boards"]["denied"], 2)
        with self.assertRaisesRegex(ValueError, "replace-access-policy"):
            self.run_discovery(lambda *_: self.fail("changed scope made a request"), plan=plan,
                               mode="validate", prefixes=(get_only,))
        self.reopen()
        _, calls = self.run_discovery(lambda *_: self.fail("omitted flags removed a denial"),
                                      plan=plan, mode="validate")
        self.assertEqual(calls, [])
        result, calls = self.run_discovery(handler, plan=plan, mode="validate", prefixes=(entire,),
                                           replace_policy=True)
        self.assertEqual([(m, path) for m, _, path, _, _ in calls], [("GET", "/v0/postings/head-only")])
        self.assertEqual((result["boards"]["validated"], result["boards"]["denied"]), (1, 1))

    def test_legacy_head_only_checks_require_get_and_cannot_replace_verified_spelling(self):
        plan = Plan(PLAN.through, recent_days=0)
        self.index_lever("Mixed", "historical", plan=plan)
        path = self.path / "ats-registry.json"
        registry = empty_registry()
        proof = {"kind": "source_audit_link", "url": "https://example.invalid/jobs"}
        register(registry, "https://jobs.lever.co/mixed", proof)
        register(registry, "https://jobs.lever.co/historical", proof)
        registry["boards"]["lever:mixed"].update(last_checked_run=1, discovery_verification={
            "method": "get_board_schema", "url": "https://jobs.lever.co/mixed",
        })
        registry["boards"]["lever:historical"]["discovery_verification"] = {
            "method": "head_reachable", "url": "https://jobs.lever.co/historical",
        }
        save_registry(path, registry)
        self.state.db.execute("DELETE FROM board_discovery_policy")  # Simulate pre-upgrade store.
        self.state.db.execute("""
            UPDATE board_discovery_board SET verified_url='https://jobs.lever.co/historical',
                verified_at=?, verification='head_reachable' WHERE board_key='lever:historical'
        """, (utc(self.now),))
        self.state.db.execute("""
            UPDATE board_discovery_variant SET status='validated', reason='head_reachable'
            WHERE board_key='lever:historical'
        """)
        self.reopen()
        with self.assertRaisesRegex(ValueError, "Legacy campaign has no saved restrictions"):
            self.run_discovery(lambda *_: self.fail("legacy policy was silently presumed"), plan=plan, mode="validate")
        self.state.publish(path)
        current = read_registry(path)
        self.assertEqual(current["boards"]["lever:historical"]["discovery_verification"]["method"], "pending_get")
        self.assertEqual(current["boards"]["lever:mixed"], registry["boards"]["lever:mixed"])

        def handler(method, url, params):
            if url.path == "/robots.txt":
                return wire(status=404)
            if method == "HEAD":
                self.assertTrue(url.path.endswith("/Mixed"))
                return reached(method, url)
            if url.path.endswith("/historical"):
                return reached(method, url)
            return wire({"error": "tenant does not exist"})

        result, calls = self.run_discovery(handler, plan=plan, mode="validate", replace_policy=True)
        self.assertEqual([(m, path.split("/")[-1]) for m, _, path, _, _ in calls],
                         [("GET", "robots.txt"), ("HEAD", "Mixed"), ("GET", "Mixed"), ("GET", "historical")])
        self.assertEqual((result["boards"]["validated"], result["boards"]["failed"]), (1, 1))
        self.state.publish(path)
        current = read_registry(path)
        self.assertEqual(current["boards"]["lever:historical"]["discovery_verification"]["method"], "get_board_schema")
        self.assertEqual(current["boards"]["lever:mixed"], registry["boards"]["lever:mixed"])

    def test_expired_robots_denials_recheck_streams_and_variants_only(self):
        initial = self.now
        plan = Plan(PLAN.through, recent_days=0)
        withheld = "https://api.lever.co/v0/postings/withheld"

        def handler(method, url, params):
            fresh = self.now >= initial + ROBOTS_TTL
            if url.path == "/robots.txt":
                if not fresh and url.hostname == "web.archive.org":
                    return wire(b"User-agent: *\nDisallow: /cdx/search/cdx?url=jobs.ashbyhq.com\n")
                if not fresh and url.hostname == "api.lever.co":
                    return wire(b"User-agent: *\nDisallow: /v0/postings/blocked\n")
                return wire(status=404)
            if url.hostname == "web.archive.org":
                host = params["url"][0]
                if host == "jobs.lever.co":
                    return cdx(*(f"https://{host}/{slug}" for slug in ("blocked", "withheld")))
                return cdx(f"https://{host}/candidate") if host in {"jobs.eu.lever.co", "jobs.ashbyhq.com"} else cdx()
            if url.hostname == "api.eu.lever.co":
                self.assertFalse(fresh, "publisher denial was cleared")
                return wire(status=403)
            self.assertFalse(url.path.endswith("/withheld"))
            return reached(method, url)

        first, _ = self.run_discovery(handler, plan=plan, prefixes=(withheld,))
        self.assertEqual(first["boards"]["denied"], 3)
        self.reopen()
        self.sleep(ROBOTS_TTL - 1)
        _, calls = self.run_discovery(handler, plan=plan)
        self.assertEqual(calls, [])
        self.sleep(2)
        refreshed, calls = self.run_discovery(handler, plan=plan)
        self.assertEqual((refreshed["boards"]["validated"], refreshed["boards"]["denied"]), (2, 2))
        for origin in ("web.archive.org", "api.lever.co"):
            self.assertEqual(next(path for _, host, path, _, _ in calls if host == origin), "/robots.txt")
        self.assertFalse(any(host == "api.eu.lever.co" or path.endswith("/withheld") for _, host, path, _, _ in calls))

    def test_dispatch_contention_and_restart_respect_actual_start(self):
        plan = Plan(PLAN.through, recent_days=0)
        self.index_lever("one", "two", plan=plan)
        host = "api.lever.co"
        self.state.save_robots(host, self.now + ROBOTS_TTL, RobotsRules("User-agent: *\nCrawl-delay: 1", "Prospect"))
        reserve = self.state.set_dispatch_after

        def delayed(origin, not_before):
            if not delayed.called:
                delayed.called = True
                self.sleep(2)
            reserve(origin, not_before)

        delayed.called = False
        with patch.object(self.state, "set_dispatch_after", side_effect=delayed):
            _, first = self.run_discovery(lambda m, u, _: reached(m, u), plan=plan, mode="validate", requests=1,
                                          origin_concurrency={host: 2}, concurrency=2)
        self.reopen()
        waiting, calls = self.run_discovery(lambda *_: self.fail("resumed too soon"), plan=plan,
                                            mode="validate", idle_seconds=0)
        self.assertEqual((waiting["stop_reason"], calls), ("origin_cooldown", []))
        self.sleep(1)
        _, next_call = self.run_discovery(lambda m, u, _: reached(m, u), plan=plan, mode="validate", requests=1)
        self.assertEqual(next_call[0][4] - first[0][4], 1)

    def test_concurrent_origin_starts_are_paced_while_first_request_is_active(self):
        plan = Plan(PLAN.through, recent_days=0)
        self.index_lever("one", "two", plan=plan)
        host = "api.lever.co"
        self.state.save_robots(host, time.time() + ROBOTS_TTL, RobotsRules("User-agent: *\nCrawl-delay: 0.1", "Prospect"))
        second = threading.Event()

        def handler(method, url, params):
            if url.path.endswith("/one"):
                self.assertTrue(second.wait(4))
            else:
                second.set()
            return wire(status=404)

        transport = FixtureTransport(handler, time.monotonic)
        config = Limits(2, 1, concurrency=2, timeout=5, origin_delays={host: 0}, origin_concurrency={host: 2})
        Discovery(self.state, transport, "test", plan, config, mode="validate").run()
        self.assertEqual([call[2] for call in transport.calls], ["/v0/postings/one", "/v0/postings/two"])
        self.assertGreaterEqual(transport.calls[1][4] - transport.calls[0][4], .099)

    def test_publisher_stop_revokes_reserved_dispatch_that_has_not_started(self):
        plan = Plan(PLAN.through, recent_days=0)
        self.index_lever("one", "two", plan=plan)
        prepared, stopped = threading.Event(), threading.Event()
        test = self

        class DelayedPreparation(FixtureTransport):
            def request(self, method, url, byte_limit, seconds, *, before_send):
                if url.endswith("/two?mode=json&limit=1"):
                    prepared.set()
                    test.assertTrue(stopped.wait(5))
                return super().request(method, url, byte_limit, seconds, before_send=before_send)

        def handler(method, url, params):
            if url.path == "/robots.txt":
                return wire(status=404)
            self.assertTrue(prepared.wait(5))
            return wire(cf_mitigated="challenge")

        transport = DelayedPreparation(handler, lambda: self.now)
        set_origin = self.state.set_origin

        def track_stop(origin, not_before, stop):
            set_origin(origin, not_before, stop)
            if stop:
                stopped.set()

        limits = Limits(10, 1, concurrency=2, origin_delays={"api.lever.co": 0},
                        origin_concurrency={"api.lever.co": 2})
        with patch.object(self.state, "set_origin", side_effect=track_stop):
            report = Discovery(self.state, transport, "test", plan, limits, mode="validate",
                               clock=lambda: self.now, monotonic=lambda: self.now, sleep=self.sleep).run()
        self.assertEqual([call[2] for call in transport.calls], ["/robots.txt", "/v0/postings/one"])
        self.assertEqual(report["boards"]["denied"], 2)

    def test_blocked_request_releases_campaign_at_deadline_and_cannot_commit_late(self):
        plan = Plan(PLAN.through, recent_days=0)
        self.index_lever("slow", "other", plan=plan)
        self.run_discovery(lambda m, u, _: wire(status=404) if u.path == "/robots.txt" else reached(m, u),
                           plan=plan, mode="validate", requests=2)
        for campaign_bound in (False, True):
            with self.subTest(campaign_bound=campaign_bound):
                release = threading.Event()

                def block(method, url, params):
                    self.assertEqual(method, "GET")
                    self.assertTrue(release.wait(4))
                    return wire([])

                transport = FixtureTransport(block, time.monotonic)
                limits = Limits(4, 1, concurrency=2, bytes=100, response_bytes=100,
                                seconds=.2 if campaign_bound else 3, timeout=3 if campaign_bound else .2,
                                origin_delays={"api.lever.co": 0})
                runner = Discovery(self.state, transport, "test", plan, limits, mode="validate", clock=lambda: self.now)
                start = time.monotonic()
                try:
                    result = runner.run()
                    self.assertLess(time.monotonic() - start, 1.5)
                    self.assertEqual(result["stop_reason"], "time_budget" if campaign_bound else "request_deadline")
                    self.assertEqual((result["requests"], result["bytes_read"], result["boards"]["validated"]),
                                     (1, 100, 0))
                    with self.assertRaisesRegex(RuntimeError, "already active"):
                        with self.state.exclusive():
                            self.fail("second executor acquired the store during the physical call")
                finally:
                    release.set()
                    for future in runner.abandoned:
                        future.result(timeout=4)
                self.assertEqual(self.state.summary("test")["boards"]["validated"], 0)
                attempt = self.state.db.execute("SELECT reason,http_status FROM board_discovery_attempt ORDER BY id DESC").fetchone()
                self.assertEqual(tuple(attempt), ("request_deadline", 0))
                # Keep the same GET at the front for the second deadline scenario.
                self.state.db.execute("UPDATE board_discovery_variant SET last_attempt=0 WHERE board_key='lever:slow'")

    def test_error_body_challenge_stops_origin_without_retaining_payload(self):
        plan = Plan(PLAN.through, recent_days=0)
        self.index_lever("first", "second", plan=plan)
        self.run_discovery(lambda m, u, _: wire(status=404) if u.path == "/robots.txt" else reached(m, u),
                           plan=plan, mode="validate", requests=2)
        body = b"<title>Just a moment</title>fixture-private-challenge-body"
        reply = Mock(status_code=503, headers={"Content-Type": "text/html", "Retry-After": "60"})
        reply.__enter__ = Mock(return_value=reply)
        reply.__exit__ = Mock()
        reply.raw.read.return_value = body
        config = Limits(10, 1, origin_delays={"api.lever.co": 0})
        with RequestsTransport("ops@example.invalid") as transport:
            with patch.object(transport.sessions["api.lever.co"].get_adapter("https://api.lever.co"), "send",
                              return_value=reply) as send:
                report = Discovery(self.state, transport, "test", plan, config, mode="validate",
                                   clock=lambda: self.now, monotonic=lambda: self.now, sleep=self.sleep).run()
                send.assert_called_once()
        reply.raw.read.assert_called_once_with(8192, decode_content=False)
        self.assertEqual(report["boards"]["denied"], 2)
        self.assertEqual(self.state.origin("api.lever.co")[1], "challenge")
        attempt = self.state.db.execute("SELECT reason,http_status,bytes_read FROM board_discovery_attempt ORDER BY id DESC").fetchone()
        self.assertEqual(tuple(attempt), ("challenge", 503, len(body)))
        self.assertNotIn(body.decode(), "\n".join(self.state.db.iterdump()))
        self.sleep(ROBOTS_TTL + 1)
        self.reopen()
        _, calls = self.run_discovery(lambda *_: self.fail("retried challenge"), plan=plan, mode="validate")
        self.assertEqual(calls, [])

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
            return reached(method, url)

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
        self.assertEqual(len(calls), 8)

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
            return reached(method, url)

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
            if method == "HEAD":
                pending.wait(timeout=5)
                if slug == "One":
                    return wire(cf_mitigated="challenge")
                self.assertTrue(challenge_recorded.wait(timeout=5))
            elif slug == "One":
                return wire(cf_mitigated="challenge")
            return reached(method, url)

        with patch.object(self.state, "set_origin", side_effect=track_stop):
            result, _ = self.run_discovery(handler, plan=plan, mode="validate", concurrency=8,
                                           origin_concurrency={"api.lever.co": 2})
        self.assertEqual(probed, {"One", "two"})
        self.assertEqual(result["boards"]["validated"], 0)
        self.assertEqual(result["boards"]["denied"], 3)
        self.assertEqual(self.state.origin("api.lever.co")[1], "challenge")

    def test_publication_exceeds_legacy_seed_cap_and_cli_is_offline(self):
        plan = Plan(PLAN.through, recent_days=0, page_size=6_000)

        def handler(method, url, params):
            if url.path == "/robots.txt":
                return wire(status=404)
            if url.hostname == "api.lever.co":
                return reached(method, url)
            host = params["url"][0]
            return cdx(*(f"https://{host}/tenant-{i}" for i in range(5_050))) if host == "jobs.lever.co" else cdx()

        result, _ = self.run_discovery(handler, plan=plan, requests=12_000, concurrency=4)
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
        self.assertEqual(len(validate_registry(read_registry(registry))["boards"]), 5_050)
        incoming = {"url": "https://example.invalid/job", "source": "fixture-audit", "location": "Washington, DC",
                    "employer_board_urls": ["https://fixture.applytojob.com/apply"]}
        base = self.path / "base.json"
        base.write_text(json.dumps({"records": [incoming], "stats": {}}))
        expand(base, registry, self.path / "ats", self.path / "combined.json", board_budget=1, seed_queries=0,
               transport=legacy_transport(Session()))
        self.assertEqual(self.state.publish(registry)["registry_boards"], 5_051)
        reloaded = read_registry(registry)
        self.assertIn("lever:tenant-5049", reloaded["boards"])
        self.assertEqual(reloaded["boards"]["jazzhr:fixture"]["evidence"][0]["kind"], "source_audit_link")

    def test_independent_stores_serialize_registry_read_modify_write(self):
        plan = Plan(PLAN.through, recent_days=0)
        path = self.path / "ats-registry.json"
        save_registry(path, empty_registry())
        first_read, second_started, release_first, second_read = (threading.Event() for _ in range(4))
        count = 0

        def delayed_read(registry):
            nonlocal count
            snapshot = read_registry(registry)
            count += 1
            if count == 1:
                first_read.set()
                self.assertTrue(release_first.wait(5))
            else:
                second_read.set()
            return snapshot

        def publish(tenant):
            with Store.in_data_dir(self.path / tenant) as store:
                state = DiscoveryState(store)

                def handler(method, url, params):
                    if url.path == "/robots.txt":
                        return wire(status=404)
                    if url.hostname == "web.archive.org":
                        return cdx(f"https://jobs.lever.co/{tenant}") if params["url"] == ["jobs.lever.co"] else cdx()
                    return reached(method, url)

                transport = FixtureTransport(handler, lambda: self.now)
                limits = Limits(20, 10, origin_delays=dict.fromkeys(DELAYS, 0))
                Discovery(state, transport, "test", plan, limits, clock=lambda: self.now,
                          monotonic=lambda: self.now, sleep=self.sleep).run()
                if tenant == "second":
                    second_started.set()
                return state.publish(path)

        with patch("career_engine.board_discovery_state.read_registry", side_effect=delayed_read):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(publish, "first")
                self.assertTrue(first_read.wait(5))
                second = pool.submit(publish, "second")
                try:
                    self.assertTrue(second_started.wait(5))
                    with path.with_name(path.name + ".lock").open("rb") as lock:
                        with self.assertRaises(BlockingIOError):
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.assertFalse(second_read.wait(.1))
                finally:
                    release_first.set()
                self.assertEqual(first.result(timeout=5)["registry_boards"], 1)
                self.assertEqual(second.result(timeout=5)["registry_boards"], 2)
        self.assertEqual(set(read_registry(path)["boards"]), {"lever:first", "lever:second"})


class TransportTests(unittest.TestCase):
    def test_late_headers_and_late_eof_are_rejected(self):
        url = "https://api.lever.co/v0/postings/tenant"
        for late_at in ("headers", "eof"):
            with self.subTest(late_at=late_at):
                now = 0
                reply = Mock(status_code=200, headers={"Content-Type": "application/json"})
                reply.__enter__ = Mock(return_value=reply)
                reply.__exit__ = Mock()

                def send(*args, **kwargs):
                    nonlocal now
                    if late_at == "headers":
                        now = 5
                    return reply

                def read(*args, **kwargs):
                    nonlocal now
                    now = 5
                    return b""

                reply.raw.read.side_effect = read
                with RequestsTransport("ops@example.invalid") as transport:
                    adapter = transport.sessions["api.lever.co"].get_adapter(url)
                    with patch.object(adapter, "send", side_effect=send), patch(
                            "career_engine.board_discovery_http.time.monotonic", side_effect=lambda: now):
                        result = transport.request("HEAD" if late_at == "headers" else "GET", url, 100, 1)
                self.assertEqual(result, Response(error="request_deadline"))
                reply.__exit__.assert_called_once()
                if late_at == "headers":
                    reply.raw.read.assert_not_called()

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
