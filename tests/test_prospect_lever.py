"""Synthetic Lever inventories through the existing transport and Prospect store."""
import hashlib
import json
import os
import subprocess
import sys
import unittest
from collections import Counter
from unittest.mock import patch

from career_engine.ats import PublicTransport, board_from_url, retrieve_board
from career_engine.geography import DEFAULT_VERSION
from career_engine.relabel import relabel
from career_engine.store.collectors import collect_lever
from test_prospect_store import CODE_SHA, Response, Session, StoreTestCase, read_rows, snapshot_files


BOARD = board_from_url("https://jobs.lever.co/synthetic-lever")
EU_BOARD = board_from_url("https://jobs.eu.lever.co/synthetic-lever")
OTHER_BOARD = board_from_url("https://jobs.lever.co/synthetic-other")
SECRET = "synthetic-credential-do-not-persist"


def native(number):
    return f"00000000-0000-0000-0000-{number:012x}"


def job(number, *, board=BOARD, location="Washington, DC", alternatives=(), **fields):
    return {"id": native(number), "text": "Program Associate", "hostedUrl": f"{board['url']}/{native(number)}",
            "categories": {"location": location, "allLocations": list(alternatives)},
            "descriptionPlain": "Synthetic public job description", **fields}


def text_response(text, content_type):
    response = Response(None)
    response.content = text.encode()
    response.headers["Content-Type"] = content_type
    return response


def board_title():
    return (Response(None, 404),
            text_response("<title>Synthetic Civic Jobs - Lever</title>", "text/html"))


class LeverStoreTests(StoreTestCase):
    def collect(self, *responses, board=BOARD, max_pages=3, max_requests=180):
        session = Session(*responses)
        transport = PublicTransport(session, pause=lambda _: None, max_requests=max_requests)
        run_id = self.run_id()
        rows, stats = collect_lever(self.store, run_id, transport, board, max_pages=max_pages)
        self.store.end_run(run_id)
        poll = self.store.connection.execute("SELECT * FROM poll WHERE run_id = ?", (run_id,)).fetchone()
        self.advance()
        return rows, stats, poll, session.calls

    def test_opt_in_preserves_requests_and_legacy_brief_rows(self):
        page = Response([job(1), job(1, location="London, UK"), job(2)])

        def retrieve():
            session = Session(page, *board_title())
            return (*retrieve_board(PublicTransport(session, pause=lambda _: None), BOARD), session.calls)

        with patch("career_engine.ats.stamp", return_value="2026-11-18T14:00:00Z"):
            with patch.dict(os.environ, {}, clear=True):
                legacy = retrieve()
            with patch.dict(os.environ, {"PROSPECT_DATA_DIR": str(self.data), "PROSPECT_CODE_SHA": CODE_SHA}, clear=True):
                durable = retrieve()
        self.assertEqual(durable, legacy)
        self.assertEqual([row["source_id"] for row in durable[0]], [f"synthetic-lever:{native(n)}" for n in (1, 2)])
        db = self.store.connection
        self.assertEqual(db.execute("SELECT count(*) FROM sighting").fetchone()[0], 3)
        self.assertEqual(db.execute("SELECT canonical_name, status FROM employer").fetchone()[:],
                         ("Synthetic Civic", "provisional"))
        self.assertEqual([row["role"] for row in db.execute("SELECT role FROM poll_capture ORDER BY ordinal")],
                         ["inventory", "board"])

    def test_pagination_duplicates_and_stable_identity_across_polls_tenants_and_regions(self):
        first = Response([job(n) for n in range(100)])
        repeat = 95
        repeated_id = job(repeat, location="London, UK", id=native(repeat).upper())
        second = Response([repeated_id, job(100)])
        _, _, poll, calls = self.collect(first, second, *board_title())
        self.assertEqual([(url, opts["params"]) for url, opts in calls[:2]], [
            ("https://api.lever.co/v0/postings/synthetic-lever", {"mode": "json", "limit": 100, "skip": skip})
            for skip in (0, 100)
        ])
        self.assertEqual((poll["status"], poll["received"], poll["declared_total"]), ("ok", 102, None))
        db = self.store.connection
        self.assertEqual([(row["raw_sha"], row["sightings"]) for row in db.execute(
            "SELECT raw_sha, sightings FROM poll_inventory ORDER BY ordinal"
        )], [(hashlib.sha256(page.content).hexdigest(), count) for page, count in [(first, 100), (second, 2)]])
        for page in (first, second):
            self.assertEqual(self.store.blobs.get(hashlib.sha256(page.content).hexdigest()), page.content)
        before = {row["identity_key"]: dict(row) for row in db.execute("SELECT * FROM posting")}
        self.assertEqual(len(before), 101)

        self.collect(Response([job(repeat)]), *board_title())
        empty = Response([])
        rows, _, empty_poll, _ = self.collect(empty)
        self.assertEqual(rows, [])
        self.assertEqual((empty_poll["status"], empty_poll["received"], empty_poll["raw_sha"]),
                         ("ok", 0, hashlib.sha256(empty.content).hexdigest()))
        for board in (OTHER_BOARD, EU_BOARD):
            _, _, _, requests = self.collect(Response([job(repeat, board=board)]), *board_title(), board=board)
            expected_host = "api.eu.lever.co" if board == EU_BOARD else "api.lever.co"
            self.assertEqual(requests[0][0], f"https://{expected_host}/v0/postings/{board['token']}")
        bundle = self.export()
        after = {row["identity_key"]: row for row in read_rows(bundle, "postings")}
        key = f"{BOARD['key']}:{native(repeat)}"
        self.assertEqual(len(after), 103)
        self.assertEqual(after[key]["posting_id"], before[key]["posting_id"])
        self.assertEqual(after[key]["first_seen"], before[key]["first_seen"])
        self.assertGreater(after[key]["last_seen"], before[key]["last_seen"])
        self.assertEqual(after[f"{BOARD['key']}:{native(100)}"]["last_seen"],
                         before[f"{BOARD['key']}:{native(100)}"]["last_seen"])
        self.assertTrue(all(row["closed_observed_at"] is None for row in after.values()))
        self.assertEqual(len(read_rows(bundle, "employers")), 3)
        self.assertEqual(len({after[f"{board['key']}:{native(repeat)}"]["employer_id"]
                              for board in (BOARD, OTHER_BOARD, EU_BOARD)}), 3)
        self.assertEqual(Counter(row["poll_id"] for row in read_rows(bundle, "sightings"))[poll["poll_id"]], 102)

    def test_reference_role_cannot_be_reused_as_discovery(self):
        self.channel(platform="lever", locator=BOARD["url"], channel_key=BOARD["key"], evidence_role="reference")
        session = Session(Response([job(1)]))
        run_id = self.run_id()
        with self.assertRaisesRegex(ValueError, "evidence role conflicts"):
            collect_lever(self.store, run_id, PublicTransport(session, pause=lambda _: None), BOARD)
        self.assertEqual(session.calls, [])
        self.assertEqual(self.store.connection.execute("SELECT count(*) FROM poll").fetchone()[0], 0)

    def test_missing_second_page_capture_cannot_borrow_first_pages_digest(self):
        class MissingCaptureTransport(PublicTransport):
            def json(self, url, params=None, **options):
                if params["skip"] == 100:
                    options.pop("capture")
                return super().json(url, params, **options)

        session = Session(Response([job(n) for n in range(100)]), Response([job(100)]), *board_title())
        run_id = self.run_id()
        collect_lever(self.store, run_id, MissingCaptureTransport(session, pause=lambda _: None), BOARD)
        self.store.end_run(run_id)
        with self.assertRaisesRegex(ValueError, "unexplained missing inventory response"):
            self.export()

    def test_malformed_rows_partial_pagination_and_failures(self):
        no_title = job(1, text="")
        conflict = job(2, hostedUrl=f"{BOARD['url']}/{native(1)}")
        url_only = job(3, id=None)
        no_identity = {"text": "A title cannot identify a posting"}
        _, _, malformed, _ = self.collect(Response([no_title, conflict, url_only, no_identity, 3]), *board_title())
        page = Response([job(n) for n in range(100)])
        _, _, interrupted, _ = self.collect(page, Response(None, 503))
        _, _, capped, _ = self.collect(page, *board_title(), max_pages=1)
        _, _, repeated, _ = self.collect(page, page, *board_title())
        _, _, failed, _ = self.collect(Response(None, 503))
        _, _, wrong_shape, _ = self.collect(Response({"postings": []}))
        invalid_json = text_response("invalid public JSON", "application/json")
        _, _, undecodable, _ = self.collect(invalid_json)
        _, _, skipped, _ = self.collect(max_requests=0)
        self.assertEqual([p["status"] for p in (malformed, interrupted, capped, repeated, failed,
                                                wrong_shape, undecodable, skipped)],
                         ["partial"] * 4 + ["failed"] * 3 + ["skipped:request_budget"])
        self.assertEqual(repeated["received"], 200)
        self.assertEqual(undecodable["raw_sha"], hashlib.sha256(invalid_json.content).hexdigest())
        bundle = self.export()
        polls = {row["poll_id"]: row for row in read_rows(bundle, "polls")}
        counts = Counter(row["poll_id"] for row in read_rows(bundle, "sightings"))
        for poll_id, row in polls.items():
            self.assertEqual(row["received"], counts[poll_id] + row["unrepresented"])
        self.assertEqual(polls[malformed["poll_id"]]["unrepresented"], 2)
        sightings = {row["native_id"]: row for row in read_rows(bundle, "sightings")
                     if row["poll_id"] == malformed["poll_id"]}
        self.assertEqual(set(sightings), {native(n) for n in (1, 2, 3)})
        self.assertTrue(sightings[native(2)]["url"].startswith("https://redacted.invalid/postings/"))
        self.assertEqual(sightings[native(3)]["url"], url_only["hostedUrl"])

    def geography_and_privacy_bundle(self):
        safe_page = Response([
            job(1, location="New York, NY", alternatives=["Arlington, VA", "New York, NY"]),
            job(2, location="United States", workplaceType="remote"),
            job(3, text="private-contact@example.invalid",
                hostedUrl=f"{BOARD['url']}/{native(3)}?access_token=synthetic-signed-link"),
            *[job(n) for n in range(100, 197)],
        ])
        withheld_page = Response([
            job(4, location="Washington, DC", workplaceType="remote", descriptionPlain=SECRET),
            {"text": "Unrepresentable synthetic record"},
        ])
        with patch.dict(os.environ, {"PROSPECT_TEST_SECRET": SECRET}, clear=True):
            session = Session(safe_page, withheld_page, *board_title())
            transport = PublicTransport(session, pause=lambda _: None)
            run_id = self.run_id()
            rows, _ = collect_lever(self.store, run_id, transport, BOARD)
            self.store.end_run(run_id)
        self.assertNotIn(SECRET, json.dumps(rows))
        self.assertEqual(next(row["description_sha256"] for row in rows
                              if row["identity"].endswith(native(4))), "")
        self.assertNotIn("sha256", transport.requests[1])
        self.assertEqual(transport.requests[1]["capture_withheld"], "secret_echo")
        poll = self.store.connection.execute("SELECT * FROM poll WHERE run_id = ?", (run_id,)).fetchone()
        self.assertEqual(poll["status"], "partial")
        self.assertFalse(self.store.blobs.exists(hashlib.sha256(withheld_page.content).hexdigest()))
        self.assertEqual(relabel(self.store, "geo", DEFAULT_VERSION), 101)
        self.assertEqual(relabel(self.store, "geo", DEFAULT_VERSION), 0)
        return self.export(), hashlib.sha256(safe_page.content).hexdigest()

    def test_export_and_geography_use_safe_evidence_even_when_a_page_is_withheld(self):
        bundle, safe_sha = self.geography_and_privacy_bundle()
        poll, = read_rows(bundle, "polls")
        self.assertEqual((poll["status"], poll["received"], poll["raw_sha"], poll["capture_withheld"],
                          poll["unrepresented"]), ("partial", 102, safe_sha, "secret_echo", 1))
        sightings = {row["native_id"]: row for row in read_rows(bundle, "sightings")}
        labels = {row["subject_id"]: row["value"] for row in read_rows(bundle, "labels")}
        self.assertEqual(sightings[native(1)]["location_raw"], "New York, NY ; Arlington, VA")
        self.assertEqual(labels[sightings[native(1)]["sighting_id"]], "dmv_va")
        self.assertEqual(labels[sightings[native(2)]["sighting_id"]], "remote_us_eligible")
        self.assertEqual(labels[sightings[native(4)]["sighting_id"]], "remote_eligibility_unknown")
        self.assertEqual(sightings[native(4)]["location_raw"], "Remote ; Washington, DC")
        self.assertIsNone(sightings[native(4)]["raw_sha"])
        self.assertIsNone(sightings[native(4)]["description_sha"])
        self.assertEqual(sightings[native(3)]["url"], f"{BOARD['url']}/{native(3)}")
        content = b"".join(snapshot_files(bundle).values())
        for private in (SECRET, "synthetic-signed-link", "private-contact@example.invalid",
                        "Unrepresentable synthetic record", "Synthetic public job description"):
            self.assertNotIn(private.encode(), content)

    @unittest.skipUnless(os.environ.get("PROSPECT_EVAL_CHECKOUT"), "requires the independent evaluator checkout")
    def test_independent_evaluator_accepts_lever_export(self):
        bundle, _ = self.geography_and_privacy_bundle()
        result = subprocess.run([sys.executable, "-m", "contract.validate", str(bundle), "--json"],
                                cwd=os.environ["PROSPECT_EVAL_CHECKOUT"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])
