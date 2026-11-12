"""Offline JazzHR intake, audit, privacy, and contract regressions."""
import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from career_engine.ats import PublicTransport, board_from_url, retrieve_board
from career_engine.ats_registry import empty_registry
from career_engine.geography import DEFAULT_VERSION
from career_engine.public_sources import discover_jazz_sitemap
from career_engine.relabel import relabel
from career_engine.store.jazzhr import collect_jazzhr
from test_prospect_store import CODE_SHA, START, Response, Session, StoreTestCase, read_rows, snapshot_files


FIXTURES = Path(__file__).parent / "fixtures" / "jazzhr"
BOARD = board_from_url("https://synthetic-jazz.applytojob.com/apply")
POST = f"{BOARD['url']}/AbC01234/Program-Associate"
SECRET = "synthetic-configured-jazzhr-credential"


def fixture(name):
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


def html_response(text, status=200):
    response = Response(None, status)
    response.content = text.encode()
    response.headers["Content-Type"] = "text/html"
    return response


def redirect(target):
    response = Response(None, 302)
    response.headers["Location"] = target
    return response


class JazzHRStoreTests(StoreTestCase):
    def collect(self, *responses, board=BOARD, detail_budget=0, max_requests=180):
        session = Session(*responses)
        transport = PublicTransport(session, pause=lambda _: None, max_requests=max_requests)
        run_id = self.run_id()
        rows, stats = collect_jazzhr(self.store, run_id, transport, board, detail_budget=detail_budget)
        self.store.end_run(run_id)
        poll = self.store.connection.execute("SELECT * FROM poll WHERE run_id = ?", (run_id,)).fetchone()
        self.advance()
        return rows, stats, poll, transport, session

    def index(self, name="index", **kwargs):
        return self.collect(Response(None, 404), html_response(fixture(name)), **kwargs)

    def captures(self, poll):
        return dict(self.store.connection.execute(
            "SELECT role, raw_sha FROM poll_capture WHERE poll_id = ? ORDER BY ordinal", (poll["poll_id"],)
        ))

    def test_configured_entry_point_preserves_legacy_requests_and_brief(self):
        def retrieve():
            session = Session(Response(None, 404), html_response(fixture("index")), html_response(fixture("detail")))
            transport = PublicTransport(session, pause=lambda _: None)
            rows, stats = retrieve_board(transport, BOARD, detail_budget=1)
            return rows, stats, session.calls, transport.requests

        with patch("career_engine.jazzhr.stamp", return_value=START), patch("career_engine.ats.stamp", return_value=START):
            with patch.dict(os.environ, {}, clear=True):
                legacy = retrieve()
            with patch.dict(os.environ, {"PROSPECT_DATA_DIR": str(self.data), "PROSPECT_CODE_SHA": CODE_SHA}, clear=True):
                durable = retrieve()
        self.assertEqual(durable, legacy)
        self.assertEqual([call[0] for call in durable[2]], ["https://synthetic-jazz.applytojob.com/robots.txt",
                                                        BOARD["url"], POST])
        self.assertEqual(len(durable[0]), 2)
        self.assertEqual(self.store.connection.execute("SELECT count(*) FROM sighting").fetchone()[0], 3)

    def test_case_sensitive_tenant_identity_and_history_across_repeated_and_empty_polls(self):
        _, _, first, _, _ = self.index()
        self.assertEqual((first["status"], first["received"], first["declared_total"]), ("ok", 3, None))
        before = {row["identity_key"]: dict(row) for row in self.store.connection.execute("SELECT * FROM posting")}
        self.assertEqual(set(before), {"jazzhr:synthetic-jazz:AbC01234", "jazzhr:synthetic-jazz:abC01234"})
        self.index()
        _, legacy_stats, empty, _, _ = self.index("empty")
        self.assertEqual(legacy_stats["status"], "failed")
        self.assertEqual((empty["status"], empty["received"], empty["raw_sha"]),
                         ("ok", 0, hashlib.sha256(fixture("empty").encode()).hexdigest()))
        other = board_from_url("https://synthetic-other.applytojob.com/apply")
        self.index(board=other)
        bundle = self.export()
        postings = {row["identity_key"]: row for row in read_rows(bundle, "postings")}
        self.assertEqual(len(postings), 4)
        self.assertEqual(len({row["employer_id"] for row in postings.values()}), 2)
        for key, old in before.items():
            self.assertEqual(postings[key]["posting_id"], old["posting_id"])
            self.assertEqual(postings[key]["first_seen"], old["first_seen"])
            self.assertGreater(postings[key]["last_seen"], old["last_seen"])
        self.assertTrue(all(row["closed_observed_at"] is None for row in postings.values()))
        sightings = read_rows(bundle, "sightings")
        self.assertEqual(len(sightings), 9)
        self.assertEqual({row["location_raw"] for row in sightings if row["poll_id"] == first["poll_id"]},
                         {"Washington, DC", "Arlington, TX", "London, UK"})
        self.assertEqual({row["raw_sha"] for row in sightings if row["poll_id"] == first["poll_id"]},
                         {first["raw_sha"]})

    def test_only_positive_public_empty_evidence_produces_an_ok_zero_poll(self):
        _, _, confirmed, _, _ = self.index("empty")
        _, _, unknown, _, _ = self.collect(Response(None, 404), html_response("<title>Careers</title><main></main>"))
        hidden = '<title>Careers</title><main><div><p style="display: none">No open positions.</p></div></main>'
        _, _, hidden_empty, _, _ = self.collect(Response(None, 404), html_response(hidden))
        _, _, challenged, transport, _ = self.index("challenge")
        _, _, denied, _, session = self.collect(Response(None, 403))
        _, _, disallowed, _, disallowed_session = self.collect(html_response("User-agent: *\nDisallow: /"))
        _, _, missing, _, _ = self.collect(Response(None, 404), Response(None, 503))
        _, _, oversize, _, _ = self.collect(Response(None, 404), html_response("x" * 1_000_001))
        _, _, skipped, _, _ = self.collect(max_requests=0)
        self.assertEqual([p["status"] for p in (confirmed, unknown, hidden_empty, challenged, denied,
                                                disallowed, missing, oversize, skipped)],
                         ["ok"] + ["failed"] * 7 + ["skipped:request_budget"])
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(len(disallowed_session.calls), 1)
        self.assertEqual(challenged["raw_sha"], None)
        self.assertEqual(transport.requests[-1]["capture_rejected"], "access_challenge_not_bypassed")
        self.assertNotIn("sha256", transport.requests[-1])
        self.assertFalse(self.store.blobs.exists(hashlib.sha256(fixture("challenge").encode()).hexdigest()))
        self.assertEqual(len(read_rows(self.export(), "sightings")), 0)

    def test_job_evidence_outside_supported_cards_blocks_a_confirmed_empty_poll(self):
        fixture_html = fixture("contradictory-empty")
        variants = {
            "same-board job route": fixture_html,
            "incomplete same-board job route": fixture_html.replace("AbC01234/Analyst", "short"),
            "same-board job route in navigation": fixture_html.replace("<div><a", "<nav><a").replace("</div>", "</nav>"),
            "unparsed job card": fixture("empty").replace(
                "</main>", '<div class="list-group"><div class="list-group-item">'
                '<h3>Policy Analyst</h3><p class="list-group-item-text">Washington, DC</p></div></div></main>'
            ),
        }
        for case, html in variants.items():
            with self.subTest(case=case):
                rows, stats, poll, _, session = self.collect(Response(None, 404), html_response(html))
                self.assertEqual((rows, stats["status"], stats["errors"]),
                                 ([], "failed", ["no_public_index_rows_or_schema_changed"]))
                self.assertEqual((poll["status"], poll["received"]), ("failed", 0))
                self.assertEqual(poll["raw_sha"], hashlib.sha256(html.encode()).hexdigest())
                self.assertEqual(len(session.calls), 2)

        self.index("empty")
        unrelated = fixture("empty").replace(
            "</main>", '<a href="/apply/">Careers</a>'
            '<a href="https://another-tenant.applytojob.com/apply/AbC01234/Analyst">Other board</a>'
            '<div hidden><a href="/apply/AbC01234/Analyst">Hidden template link</a></div></main>'
        )
        self.collect(Response(None, 404), html_response(unrelated))
        self.assertCountEqual([(poll["status"], poll["received"]) for poll in read_rows(self.export(), "polls")],
                              [("failed", 0)] * len(variants) + [("ok", 0)] * 2)

    def test_report_cap_preserves_full_visible_page_and_continuation_is_partial(self):
        items = "".join(f'<li class="list-group-item"><h3><a href="/apply/Job{n:06d}/Role">Role</a></h3>'
                        '<ul class="list-group-item-text"><li>London, UK</li></ul></li>' for n in range(1001))
        index = f'<title>Careers</title><ul class="list-group">{items}</ul>'
        rows, stats, capped, _, _ = self.collect(Response(None, 404), html_response(index))
        self.assertTrue(stats["capped"])
        self.assertEqual(len(rows), 1000)
        self.assertEqual((capped["status"], capped["received"]), ("ok", 1001))
        more = fixture("index").replace("</main>", '<a rel="next" href="?page=2">Next</a></main>')
        _, _, partial, _, session = self.collect(Response(None, 404), html_response(more))
        self.assertEqual((partial["status"], partial["received"]), ("partial", 3))
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(len(read_rows(self.export(), "sightings")), 1004)

    def test_sitemap_seeds_never_create_durable_sightings(self):
        response = html_response(f"<urlset><url><loc>{POST}</loc></url></urlset>")
        response.headers["Content-Type"] = "application/xml"
        session = Session(Response(None, 404), response)
        registry = empty_registry()
        with patch.dict(os.environ, {"PROSPECT_DATA_DIR": str(self.data)}, clear=True):
            discover_jazz_sitemap(PublicTransport(session, pause=lambda _: None), registry, page_budget=1)
        self.assertEqual(set(registry["boards"]), {BOARD["key"]})
        self.assertEqual(self.store.connection.execute("SELECT count(*) FROM poll").fetchone()[0], 0)

    def test_detail_capture_is_distinct_and_replay_keeps_original_location(self):
        _, _, poll, _, _ = self.collect(Response(None, 404), html_response(fixture("index")),
                                       html_response(fixture("detail")), detail_budget=1)
        receipts = self.captures(poll)
        self.assertEqual(set(receipts), {"inventory", "detail:AbC01234"})
        for role, name in (("inventory", "index"), ("detail:AbC01234", "detail")):
            self.assertEqual(self.store.blobs.get(receipts[role]), fixture(name).encode())
        self.assertEqual(relabel(self.store, "geo", DEFAULT_VERSION), 3)
        self.assertEqual(relabel(self.store, "geo", DEFAULT_VERSION), 0)
        bundle = self.export()
        sightings = {row["location_raw"]: row for row in read_rows(bundle, "sightings")}
        labels = {row["subject_id"]: row["value"] for row in read_rows(bundle, "labels")}
        primary = sightings["Washington, DC"]
        self.assertEqual(labels[primary["sighting_id"]], "remote_eligibility_unknown")
        self.assertEqual(primary["raw_sha"], receipts["inventory"])
        self.assertIsNotNone(primary["description_sha"])
        self.assertEqual(primary["posted_at"], "2026-11-17T12:00:00Z")
        duplicate = sightings["Arlington, TX"]
        self.assertEqual(labels[duplicate["sighting_id"]], "elsewhere")
        self.assertIsNone(duplicate["description_sha"])
        self.assertIsNone(duplicate["posted_at"])

    def test_mismatched_detail_evidence_does_not_overwrite_index(self):
        index = html_response(fixture("index"))
        wrong = POST.replace("AbC01234", "abC01234")
        mismatched = html_response(fixture("detail").replace(POST, wrong))
        private_redirect = redirect(POST + "?access_token=synthetic-private-redirect")
        private_script = html_response(fixture("detail") + '<script>const csrfToken = "synthetic-state";</script>')
        cases = ((Response(None, 503),), (mismatched,), (redirect(wrong), html_response(fixture("detail"))),
                 (private_redirect,), (html_response(fixture("challenge")),), (private_script,))
        for responses in cases:
            with self.subTest(responses=responses):
                _, stats, poll, _, session = self.collect(Response(None, 404), index, *responses, detail_budget=1)
                self.assertEqual((poll["status"], poll["received"], stats["status"]), ("partial", 3, "partial_failure"))
                self.assertEqual(set(self.captures(poll)), {"inventory"})
                self.assertFalse(any("synthetic-private-redirect" in call[0] for call in session.calls))
        sightings = read_rows(self.export(), "sightings")
        self.assertEqual(len(sightings), 18)
        self.assertTrue(all(row["description_sha"] is None for row in sightings))

    def test_replay_keeps_conflicting_index_and_detail_locations_uncertain(self):
        conflicting = fixture("detail").replace('"addressLocality": "Washington", "addressRegion": "DC"',
                                                 '"addressLocality": "New York", "addressRegion": "NY"')
        self.collect(Response(None, 404), html_response(fixture("index")), html_response(conflicting), detail_budget=1)
        relabel(self.store, "geo", DEFAULT_VERSION)
        bundle = self.export()
        local = next(row for row in read_rows(bundle, "sightings") if row["location_raw"] == "Washington, DC")
        label = next(row for row in read_rows(bundle, "labels") if row["subject_id"] == local["sighting_id"])
        self.assertEqual(label["value"], "unknown")
        self.assertIn("conflicting", label["method"])

    def privacy_bundle(self):
        _, _, malformed, _, _ = self.index("malformed")
        _, _, private, transport, _ = self.collect(Response(None, 404), html_response(fixture("index")),
                                                  html_response(fixture("private-detail")), detail_budget=1)
        self.assertEqual(set(self.captures(private)), {"inventory"})
        self.assertNotIn("sha256", transport.requests[-1])
        self.assertEqual(transport.requests[-1]["capture_rejected"], "private_page_state")
        self.assertFalse(self.store.blobs.exists(hashlib.sha256(fixture("private-detail").encode()).hexdigest()))
        echoed_index = fixture("index").replace("Program Associate", SECRET, 1)
        escaped = "".join(f"\\u{ord(char):04x}" for char in SECRET)
        echoed_detail = fixture("detail").replace("Synthetic public program duties.", escaped)
        with patch.dict(os.environ, {"PROSPECT_TEST_SECRET": SECRET}, clear=True):
            rows, _, withheld, transport, _ = self.collect(Response(None, 404), html_response(echoed_index))
            self.assertNotIn(SECRET, json.dumps(rows))
            self.assertEqual(transport.requests[-1]["capture_withheld"], "secret_echo")
            self.assertNotIn("sha256", transport.requests[-1])
            rows, _, detail_echo, transport, _ = self.collect(Response(None, 404), html_response(fixture("index")),
                                                             html_response(echoed_detail), detail_budget=1)
            self.assertNotIn(SECRET, json.dumps(rows))
            self.assertEqual(transport.requests[-1]["capture_withheld"], "secret_echo")
            self.assertNotIn("sha256", transport.requests[-1])
        for text in (echoed_index, echoed_detail):
            self.assertFalse(self.store.blobs.exists(hashlib.sha256(text.encode()).hexdigest()))
        bundle = self.export()
        polls = {row["poll_id"]: row for row in read_rows(bundle, "polls")}
        self.assertEqual((polls[malformed["poll_id"]]["received"], polls[malformed["poll_id"]]["unrepresented"]),
                         (5, 2))
        self.assertEqual((polls[withheld["poll_id"]]["status"], polls[withheld["poll_id"]]["raw_sha"],
                          polls[withheld["poll_id"]]["capture_withheld"]), ("ok", None, "secret_echo"))
        self.assertEqual((polls[detail_echo["poll_id"]]["status"], polls[detail_echo["poll_id"]]["capture_withheld"]),
                         ("partial", None))
        return bundle, malformed["poll_id"]

    def test_sensitive_metadata_and_private_capture_are_excluded_from_snapshot(self):
        bundle, malformed_id = self.privacy_bundle()
        sightings = {row["native_id"]: row for row in read_rows(bundle, "sightings") if row["poll_id"] == malformed_id}
        self.assertEqual(set(sightings), {"AbC01234", "aBc98765", "aBc01235"})
        self.assertEqual(sightings["AbC01234"]["url"], POST)
        self.assertEqual(sightings["AbC01234"]["title"], "[redacted]")
        self.assertTrue(sightings["aBc01235"]["url"].startswith("https://redacted.invalid/postings/"))
        snapshot = b"".join(snapshot_files(bundle).values())
        for private in (SECRET, "synthetic-signed-link", "synthetic-contact@example.invalid",
                        "synthetic-candidate@example.invalid", "synthetic-application-state",
                        "Synthetic public program duties."):
            self.assertNotIn(private.encode(), snapshot)

    @unittest.skipUnless(os.environ.get("PROSPECT_EVAL_CHECKOUT"), "requires the independent evaluator checkout")
    def test_independent_evaluator_accepts_jazzhr_export(self):
        bundle, _ = self.privacy_bundle()
        result = subprocess.run([sys.executable, "-m", "contract.validate", str(bundle), "--json"],
                                cwd=os.environ["PROSPECT_EVAL_CHECKOUT"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])
