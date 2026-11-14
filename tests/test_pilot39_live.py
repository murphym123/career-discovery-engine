"""Exercise the production wire and collect path with adapter-level HTTP fixtures."""
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

import requests

from career_engine.ats import FetchError
from test_pilot39 import GREEN, GH_HOST, JOBS, JAZZ, JAZZ_HOST, LEVER, LEVER_HOST, LEV_PATH, LEV_QUERY, META, SHA, address
from tools import pilot39 as pilot
from tools.pilot39_http import HTTPSWire
from tools.pilot39_run import Journal, Terms, checked_config, collect, inspect_run, purge_diagnostic_bodies

ROBOTS = b"User-agent: *\nDisallow:\n"
INDEX = "/CC-MAIN-2026-44-index"
CATALOG = address(pilot.INDEX_HOST, "/collinfo.json")
TERMS_DATE = "2026-01-01T00:00:00Z"


class WireBody(io.BytesIO):
    def __init__(self, value):
        super().__init__(value)
        self.reads = []

    def read(self, size, *, decode_content):
        assert not decode_content
        assert 0 < size <= 8192
        self.reads.append(size)
        return super().read(size)


def response(body=b"", status=200, media="application/json", **headers):
    result = requests.Response()
    result.status_code = status
    result.headers["Content-Type"] = media
    result.headers.update({key.replace("_", "-"): value for key, value in headers.items()})
    result.raw = WireBody(body)
    return result


def reviewed(arm, host, path):
    return dict(arm=arm, host=host, path=path, checked_at=TERMS_DATE,
                documentation="https://docs.example.com/public-routes", terms="https://docs.example.com/site-terms",
                reviewer="Fixture reviewer", disposition="allowed",
                retention="digest_only" if arm == "discovery" else "diagnostic_run_only")


def ads(arm, host, *paths):
    return [reviewed(arm, host, path) for path in paths]


class LiveTests(unittest.TestCase):
    def setUp(self):
        for method in ("connect", "connect_ex"):
            guard = patch.object(socket.socket, method, side_effect=AssertionError("Network disabled"))
            guard.start()
            self.addCleanup(guard.stop)
        resolver = patch("socket.getaddrinfo", side_effect=AssertionError("DNS disabled"))
        resolver.start()
        self.addCleanup(resolver.stop)
        self.now = 0.0
        clock = patch("tools.pilot39_http.time.monotonic", side_effect=lambda: self.now)
        waits = patch.object(HTTPSWire, "sleep", side_effect=self.advance)
        for stub in (clock, waits):
            stub.start()
            self.addCleanup(stub.stop)

    def advance(self, seconds):
        self.now += seconds

    def scripted(self, script):
        entries = iter(script)
        self.calls = []

        def send(adapter, request, **kwargs):
            expected, reply = next(entries, (None, None))
            self.assertEqual(expected, request.url)
            self.assertEqual(request.method, "GET")
            self.assertIsNone(request.body)
            self.assertEqual(kwargs, dict(stream=True, timeout=(5, 10), verify=True, cert=None, proxies={}))
            self.assertEqual(adapter.max_retries.total, 0)
            self.assertEqual(request.headers["Accept-Encoding"], "identity")
            self.assertTrue(request.headers["User-Agent"].startswith(pilot.USER_AGENT))
            self.assertFalse({"authorization", "proxy-authorization", "cookie", "referer"} &
                             {name.lower() for name in request.headers})
            self.calls.append((self.now, request.url))
            if isinstance(reply, BaseException):
                raise reply
            return reply

        handler = patch.object(requests.adapters.HTTPAdapter, "send", autospec=True, side_effect=send)
        handler.start()
        self.addCleanup(handler.stop)

    def transport(self, rules, stage="seeded", board=GREEN):
        output = io.StringIO()

        def journal(phase, event):
            with patch("tools.pilot39_run.os.fsync"), patch.object(output, "fileno", return_value=0):
                Journal(output)(phase, event)

        transport = pilot.LivePilotTransport(Terms(rules, TERMS_DATE), journal)
        self.assertFalse(transport.wire.session.trust_env)
        self.addCleanup(transport.close)
        if board:
            transport.stage_for_board(stage, board)
        return transport, output

    def robots(self, host, body=ROBOTS, status=200):
        return address(host, "/robots.txt"), response(body, media="text/plain", status=status)

    def test_redirect_get_is_anonymous_accounted_and_never_reads_redirect_body(self):
        initial = address(JAZZ_HOST, "/apply")
        target = initial + "/"
        first = response(b"must not read", 302, Location=target, Set_Cookie="sid=sensitive", Retry_After="1")
        redirected = response(b"<title>Example</title>", media="text/html")
        self.scripted([self.robots(JAZZ_HOST), (initial, first), (target, redirected)])
        t, output = self.transport(ads("seeded", JAZZ_HOST, "/apply"), board=JAZZ)
        with patch.dict(os.environ, {"HTTP_PROXY": "https://bad.example", "HTTPS_PROXY": "https://bad.example",
                                    "NETRC": "/no-such-file", "REQUESTS_CA_BUNDLE": "/no-such-file"}):
            self.assertIn("Example", t.get(initial))
        self.assertEqual([instant for instant, _ in self.calls], [0, 2, 4])
        self.assertEqual(t.budget.counts["seeded"], 3)
        self.assertFalse(first.raw.reads)
        self.assertTrue(first.raw.closed and redirected.raw.closed)
        self.assertNotIn("sid", output.getvalue())
        self.assertEqual(output.getvalue().count('"phase": "attempt"'), 3)

    def test_missing_terms_and_unexpected_tenant_never_construct_request(self):
        self.scripted([])
        t, output = self.transport(ads("seeded", GH_HOST, META))
        with self.assertRaisesRegex(FetchError, pilot.POLICY):
            t.get(address(GH_HOST, JOBS), {"content": "true"})
        with self.assertRaisesRegex(FetchError, "unsafe_endpoint"):
            t.get(address(GH_HOST, "/v1/boards/other"))
        with self.assertRaisesRegex(FetchError, "unsafe_endpoint"):
            t.get(address(GH_HOST, META))
        self.assertEqual(self.calls, [])
        self.assertNotIn("other", output.getvalue())
        self.assertIn('"decision": "terms_unverified"', output.getvalue())

    def test_transport_rechecks_redirect_and_respects_mixed_rule_denial(self):
        initial = address(JAZZ_HOST, "/apply")
        self.scripted([self.robots(JAZZ_HOST, b"User-agent: *\nDisallow: /apply/\n"),
                       (initial, response(status=301, Location="/apply/")),
                       self.robots(GH_HOST, b"User-agent: *\nDisallow:\nDisallow: /v1\n")])
        t, _ = self.transport(ads("seeded", JAZZ_HOST, "/apply") + ads("seeded", GH_HOST, META), board=JAZZ)
        with self.assertRaisesRegex(FetchError, "robots_disallowed"):
            t.get(initial)
        t.stage_for_board("seeded", GREEN)
        with self.assertRaisesRegex(FetchError, "robots_disallowed"):
            t.get(address(GH_HOST, META))
        self.assertEqual(len(self.calls), 3)

    def test_unsafe_redirect_does_not_stop_unrelated_public_board(self):
        # Reject the first board's unapproved redirect without sacrificing
        # the next provider's independent authorized inventory request.
        initial = address(JAZZ_HOST, "/apply")
        next_board = address(GH_HOST, META)
        self.scripted([
            self.robots(JAZZ_HOST),
            (initial, response(status=302, Location="https://other.example/careers")),
            self.robots(GH_HOST),
            (next_board, response(b'{"name":"Example"}')),
        ])
        t, output = self.transport(
            ads("seeded", JAZZ_HOST, "/apply") + ads("seeded", GH_HOST, META),
            board=JAZZ,
        )
        with self.assertRaisesRegex(FetchError, pilot.REDIRECT):
            t.get(initial)
        self.assertEqual(t.global_stop_reason(), "")
        t.stage_for_board("seeded", GREEN)
        self.assertIn("Example", t.get(next_board))
        self.assertEqual(len(self.calls), 4)
        self.assertIn("unsafe_redirect", output.getvalue())

    def test_robots_missing_and_429_stop_same_host_across_arms(self):
        target = address(GH_HOST, META)
        denial = response(status=429, Retry_After="Wed, 18 Nov 2026 14:00:00 GMT")
        self.scripted([self.robots(GH_HOST, status=410), (target, denial)])
        t, journal = self.transport(ads("seeded", GH_HOST, META) + ads("cold", GH_HOST, "/v1/boards/{tenant}"))
        with self.assertRaisesRegex(FetchError, "http_429"):
            t.get(target)
        t.stage_for_board("cold", GREEN)
        with self.assertRaisesRegex(FetchError, "host_stopped"):
            t.get(target)
        self.assertEqual(len(self.calls), 2)
        self.assertIn("2026-11-18T14:00:00Z", journal.getvalue())
        self.assertIn('"robots": "missing"', journal.getvalue())

    def test_body_stream_is_bounded_and_capture_only_sees_verified_bodies(self):
        target = address(GH_HOST, JOBS, {"content": "true"})
        too_large = response(b"x" * 10000)
        denied_encoding = response(b"cannot decode", Content_Encoding="gzip")
        fallback = address(GH_HOST, JOBS)
        self.scripted([self.robots(GH_HOST), (target, too_large), (fallback, denied_encoding)])
        t, journal = self.transport(ads("seeded", GH_HOST, JOBS))
        capture = lambda _: self.fail("Unchecked body entered capture")
        with self.assertRaisesRegex(FetchError, "response_too_large"):
            t.get(address(GH_HOST, JOBS), {"content": "true"}, max_bytes=10, capture=capture)
        self.assertEqual(too_large.raw.reads, [11])
        with self.assertRaisesRegex(FetchError, "unexpected_content_encoding"):
            t.get(fallback, capture=capture)
        self.assertEqual(denied_encoding.raw.reads, [])
        self.assertTrue(too_large.raw.closed and denied_encoding.raw.closed)
        self.assertIn('"bytes": 11', journal.getvalue())

    def test_alarm_and_stream_failure_consume_attempt_without_retry(self):
        target = address(GH_HOST, META)
        with patch("tools.pilot39_http.signal.setitimer", wraps=signal.setitimer) as timer:
            self.scripted([self.robots(GH_HOST), (target, requests.exceptions.ReadTimeout("sensitive"))])
            t, journal = self.transport(ads("seeded", GH_HOST, META))
            with self.assertRaisesRegex(FetchError, "request_timeout"):
                t.get(target)
            with self.assertRaisesRegex(FetchError, "duplicate_request"):
                t.get(target)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
        self.assertEqual([call.args[1] for call in timer.call_args_list], [25, 0, 25, 0])
        self.assertEqual(len(self.calls), 2)
        self.assertNotIn("sensitive", journal.getvalue())

    def test_absolute_timer_can_interrupt_between_chunks(self):
        target = address(GH_HOST, META)
        result = response(b'{"name":"Example"}')

        def stuck_read(*args, **kwargs):
            signal.raise_signal(signal.SIGALRM)
            self.fail("The whole-request deadline failed to interrupt streaming")

        result.raw.read = stuck_read
        self.scripted([self.robots(GH_HOST), (target, result)])
        t, _ = self.transport(ads("seeded", GH_HOST, META))
        with self.assertRaisesRegex(FetchError, "request_timeout"):
            t.get(target)
        self.assertTrue(result.raw.closed)

    def test_authentication_header_stops_host_before_capture(self):
        target = address(GH_HOST, META)
        t, journal = self.transport(ads("seeded", GH_HOST, META) + ads("cold", GH_HOST, "/v1/boards/{tenant}"))
        self.scripted([self.robots(GH_HOST), (target, response(WWW_Authenticate="Basic realm=private"))])
        with self.assertRaisesRegex(FetchError, pilot.CHALLENGE):
            t.get(target, capture=lambda _: self.fail("Challenge capture"))
        t.stage_for_board("cold", GREEN)
        with self.assertRaisesRegex(FetchError, "host_stopped_after_denial"):
            t.get(target)
        self.assertNotIn("realm", journal.getvalue())

    def test_signed_urls_and_private_json_block_before_any_capture(self):
        target = address(GH_HOST, META)
        self.scripted([self.robots(GH_HOST), (target, response(
            b'{"name":"untrusted","url":"https://example.com/file?X-Amz-Signature=private-value"}'))])
        t, journal = self.transport(ads("seeded", GH_HOST, META))
        with self.assertRaisesRegex(FetchError, pilot.PRIVATE):
            t.get(target, capture=lambda _: self.fail("Private capture"))
        self.assertEqual(t.global_stop_reason(), pilot.PRIVATE)
        self.assertNotIn("private-value", journal.getvalue())

    def test_pacing_and_stage_budget_can_block_redirect_before_dispatch(self):
        initial = address(JAZZ_HOST, "/apply")
        self.scripted([self.robots(JAZZ_HOST), (initial, response(status=308, Location="/apply/"))])
        t, _ = self.transport(ads("seeded", JAZZ_HOST, "/apply"), board=JAZZ)
        t.budget.counts["seeded"] = pilot.STAGE_LIMITS["seeded"] - 2
        with self.assertRaisesRegex(FetchError, "request_budget"):
            t.get(initial)
        t.stage_for_board("cold", JAZZ)
        self.assertEqual(t.global_stop_reason(), "request_budget")
        self.assertEqual(len(self.calls), 2)

    def test_full_collect_replays_exports_and_reconciles(self):
        host = pilot.INDEX_HOST
        token = "cold-example"
        cold_host = "boards-api.greenhouse.io"
        cold_url = f"https://job-boards.greenhouse.io/{token}"
        args = dict(url="job-boards.greenhouse.io/*", output="json", filter="status:200",
                    fl="url,timestamp,status", limit=2500, pageSize=1)
        role = dict(id="1234", title="Analyst", location={"name": "Washington, DC"},
                    absolute_url=f"{cold_url}/jobs/1234", content="Public role")
        unknown = dict(role, id="4567", absolute_url=f"{cold_url}/jobs/4567", location={"name": ""})
        rules = [
            *ads("discovery", host, "/collinfo.json", "/CC-MAIN-{collection}-index"),
            *ads("seeded", LEVER_HOST, LEV_PATH), *ads("seeded", JAZZ_HOST, "/apply"),
            *ads("cold", cold_host, "/v1/boards/{tenant}", "/v1/boards/{tenant}/jobs"),
        ]
        self.scripted([
            self.robots(host),
            (CATALOG, response(b'[{"id":"CC-MAIN-2026-44"}]')),
            (address(host, INDEX, dict(args, showNumPages="true")), response(b'{"pages":1}')),
            (address(host, INDEX, dict(args, page=0)),
             response(json.dumps({"url": cold_url, "timestamp": "20261101000000"}).encode(),
                      media="application/x-ndjson")),
            (address(host, INDEX, dict(args, url="jobs.lever.co/*", showNumPages="true")), response(b'{"pages":1}')),
            (address(host, INDEX, dict(args, url="jobs.lever.co/*", page=0)), response(b"", media="application/x-ndjson")),
            self.robots(LEVER_HOST), (address(LEVER_HOST, LEV_PATH, LEV_QUERY), response(b"[]")),
            self.robots(JAZZ_HOST), (JAZZ["url"], response(
                (Path(__file__).parent / "fixtures/jazzhr/empty.html").read_bytes(), media="text/html")),
            self.robots(cold_host),
            (address(cold_host, f"/v1/boards/{quote(token)}/jobs", {"content": "true"}),
             response(json.dumps({"jobs": [role, unknown], "meta": {"total": 2}}).encode())),
            (address(cold_host, f"/v1/boards/{quote(token)}"), response(b'{"name":"Fixture"}')),
        ])
        raw = json.dumps(dict(code_sha=SHA, terms_get_attempts=3, terms=rules)).encode()
        with tempfile.TemporaryDirectory() as scratch:
            data = Path(scratch) / "pilot"
            snapshot = collect(data, [LEVER, JAZZ], b"fixture cohort", raw, *checked_config(raw))
            self.assertEqual(len(self.calls), 13)
            self.assertEqual(len(list(snapshot.iterdir())), 8)
            runtime = data / "run"
            self.assertEqual((runtime / "config.json").read_bytes(), raw)
            manifest = json.loads((runtime / "run-manifest.json").read_text())
            self.assertEqual(manifest["config_sha256"], pilot.sha(raw))
            self.assertEqual(manifest["stage_attempts"], dict(terms=3, discovery=6, seeded=4, cold=3))
            labels = [json.loads(line) for line in (snapshot / "labels.jsonl").read_text().splitlines()]
            sightings = [json.loads(line) for line in (snapshot / "sightings.jsonl").read_text().splitlines()]
            self.assertIn("dc", {label["value"] for label in labels})
            self.assertEqual(len({label["value"] for label in labels}), 2)
            self.assertTrue(all(row["raw_sha"] and row["raw_purged"] for row in sightings))
            self.assertEqual(manifest["cold_discovered"], dict(candidates=1, new=1, selected=1))
            self.assertTrue(all(row["allowed"] == row["retrieved"] == 1 for row in manifest["board_counts"]))
            self.assertFalse(list((data / "blobs").rglob("*.gz")))
            self.assertEqual(purge_diagnostic_bodies(data), [])
            self.assertEqual([row["status"] for row in inspect_run(data)["committed_polls"]], ["ok"] * 3)
            self.assertTrue(inspect_run(data)["finalized"])
            self.assertEqual(inspect_run(data)["requests_without_result"], [])
            if evaluator := os.getenv("PROSPECT_EVAL_CHECKOUT"):
                result = subprocess.run([sys.executable, "-m", "contract.validate", str(snapshot), "--json"],
                                        cwd=evaluator, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue(json.loads(result.stdout)["ok"])

    def test_proposed_scope_skips_denied_index_and_unverified_tenant(self):
        host = pilot.INDEX_HOST
        denial = reviewed("discovery", host, "/CC-MAIN-{collection}-index")
        denial.update(disposition="robots_disallowed", documentation=f"https://{host}/robots.txt", terms="")
        unresolved = reviewed("seeded", JAZZ_HOST, "/apply")
        unresolved["disposition"] = "unverified"
        feed = "https://app.jazz.co/feeds/google/xml/0"
        rules = [denial, unresolved, reviewed("discovery", pilot.FEED_HOST, "/feeds/google/xml/0")]
        self.scripted([
            self.robots(pilot.FEED_HOST, b"User-agent: *\nDisallow: /cb\n"
                        b"Sitemap: http://app.jazz.co/feeds/google/xml/0\n"),
            (feed, response(b"<urlset></urlset>", media="application/xml")),
        ])
        raw = json.dumps(dict(code_sha=SHA, terms_get_attempts=16, terms=rules)).encode()
        with tempfile.TemporaryDirectory() as scratch:
            data = Path(scratch) / "pilot"
            collect(data, [JAZZ], b"fixture cohort", raw, *checked_config(raw))
            manifest = json.loads((data / "run/run-manifest.json").read_text())
            self.assertEqual(manifest["discovery"][0]["status"], "robots_disallowed")
            self.assertEqual(manifest["board_counts"][0]["skipped_by_reason"], {"terms_unverified": 1})
            self.assertEqual(manifest["board_counts"][0]["potential_false_exclusions"], {"terms_unverified": 1})
            self.assertEqual(len(self.calls), 2)

    def test_collect_interruption_keeps_partial_poll_and_prohibits_reuse(self):
        rules = ads("seeded", LEVER_HOST, LEV_PATH)
        listing = dict(id="01234567-89ab-cdef-0123-456789abcdef", text="Analyst",
                       hostedUrl="https://jobs.lever.co/example/01234567-89ab-cdef-0123-456789abcdef",
                       categories={"location": "Washington, DC"})
        self.scripted([self.robots(LEVER_HOST),
                       (address(LEVER_HOST, LEV_PATH, LEV_QUERY), response(json.dumps([listing] * 100).encode())),
                       (address(LEVER_HOST, LEV_PATH, dict(LEV_QUERY, skip=100)), KeyboardInterrupt())])
        raw = json.dumps(dict(code_sha=SHA, terms_get_attempts=0, terms=rules)).encode()
        with tempfile.TemporaryDirectory() as scratch:
            data = Path(scratch) / "pilot"
            with self.assertRaises(KeyboardInterrupt):
                collect(data, [LEVER], b"fixture cohort", raw, *checked_config(raw))
            with self.assertRaises(FileExistsError):
                collect(data, [LEVER], b"fixture cohort", raw, *checked_config(raw))
            result = inspect_run(data)
            self.assertFalse(result["finalized"])
            self.assertIsNone(result["runs"][0]["finished_at"])
            poll = result["committed_polls"][0]
            self.assertEqual(poll["status"], "partial")
            self.assertEqual(poll["received"], 100)
            self.assertEqual(result["polls_without_ledger_result"], [poll["poll_id"]])
            self.assertFalse((data / "exports").exists())
            self.assertFalse(list((data / "blobs").rglob("*.gz")))
            with (data / "run/operations.jsonl").open("ab") as stream:
                stream.write(b'{"truncated')
            self.assertTrue(inspect_run(data)["incomplete_journal_tail"])
