"""Offline M1a tests for persistence, collector parity, and the shared contract."""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from career_engine.ats import PublicTransport, board_from_url, retrieve_board
from career_engine.export import export_snapshot
from career_engine.regional_usajobs import collect_usajobs as collect_regional
from career_engine.sources import SourceStats, retrieve
from career_engine.store import Store
from career_engine.store.collectors import collect_usajobs

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "fixtures" / "export-v1-example"
START = "2026-11-18T14:00:00Z"
CODE_SHA = "11" * 20
CONFIG_SHA = hashlib.sha256(b"synthetic public collection settings").hexdigest()
CREDS = {"USAJOBS_API_KEY": "test-credential-should-stay-private",
         "USAJOBS_USER_AGENT": "private-user@example.invalid"}
FEDERAL_URL = "https://data.usajobs.gov/api/search"
GH_URL = "https://boards-api.greenhouse.io/v1/boards/synthetic"
GH_BOARD = board_from_url("https://job-boards.greenhouse.io/synthetic")


def read_rows(directory, entity):
    return [json.loads(line) for line in (directory / f"{entity}.jsonl").read_bytes().splitlines()]


def snapshot_files(path):
    return {file.name: file.read_bytes() for file in path.iterdir()}


def federal_job(number, location="Washington, DC", **fields):
    return {"MatchedObjectId": str(number), "MatchedObjectDescriptor": {
        "PositionTitle": "Research assistant", "OrganizationName": "Synthetic department",
        "PositionURI": f"https://www.usajobs.gov/job/{number}", "PositionLocationDisplay": location,
        "PublicationStartDate": "2026-11-17T09:00:00-05:00",
        "QualificationSummary": "Public details about the role", **fields,
    }}


def greenhouse_job(number, location="Washington, DC", **fields):
    return {"id": number, "title": "Research assistant", "location": {"name": location},
            "absolute_url": GH_BOARD["url"] + f"/jobs/{number}", **fields}


class Response:
    def __init__(self, data, status=200):
        self.content = json.dumps(data).encode()
        self.status_code = status
        self.headers = {"Content-Type": "application/json"}
        self.closed = False

    def iter_content(self, size):
        return (self.content[index:index + size] for index in range(0, len(self.content), size))

    def json(self):
        return json.loads(self.content)

    def close(self):
        self.closed = True


class Session:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)


def federal_page(total, *items):
    return Response({"SearchResult": {"SearchResultCountAll": total, "SearchResultItems": items}})


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.instant = datetime.fromisoformat(START)
        self.store = Store.in_data_dir(self.data, clock=lambda: self.instant)
        self.addCleanup(self.store.__exit__)

    def advance(self):
        self.instant += timedelta(seconds=1)

    def run_id(self, **options):
        return self.store.begin_run(code_sha=CODE_SHA, config_sha=CONFIG_SHA, **options)

    def channel(self, retention="public", **fields):
        values = dict(kind="ats", platform="greenhouse", locator=GH_BOARD["url"],
                      retention_class=retention, evidence_role="discovery", verification_state="verified")
        return self.store.ensure_channel(**(values | fields))

    def post(self, poll_id, **fields):
        values = dict(identity_key="greenhouse:synthetic:1", native_id="1",
                      url=GH_BOARD["url"] + "/jobs/1", canonical_url=GH_BOARD["url"] + "/jobs/1",
                      title="Policy researcher", employer_name_raw="Synthetic employer", location_raw="Washington")
        return self.store.record_sighting(poll_id, **(values | fields))

    def export(self, **options):
        self.advance()
        return export_snapshot(self.store, self.data / "exports", code_sha=CODE_SHA, **options)


class StoreTests(StoreTestCase):
    def test_schema_merge_foreign_keys_and_append_only_labels(self):
        self.assertEqual(self.store.schema_version, 1)
        self.assertEqual(self.store.connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        old = self.store.add_employer("Predecessor", "provisional", "test")
        successor = self.store.add_employer("Successor", "active", "test")
        self.store.merge_employer(old, successor)
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.store.merge_employer(successor, old)
        channel = self.channel(employer_id=old)
        run_id = self.run_id()
        poll = self.store.record_poll(run_id, channel, status="ok", received=1)
        with self.assertRaisesRegex(ValueError, "attempted poll"):
            self.post("missing")
        posting = self.post(poll)
        for version, value in (("geo/1", "unknown"), ("geo/2", "dc")):
            self.store.add_label(subject="posting", subject_id=posting, dimension="geo",
                                 value=value, method="test", classifier_version=version)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "subject does not exist"):
            self.store.add_label(subject="sighting", subject_id="missing", dimension="geo",
                                 value="unknown", method="test", classifier_version="geo/2")
        for statement in ("UPDATE label SET value = 'elsewhere'", "DELETE FROM label",
                          "INSERT OR REPLACE INTO label SELECT * FROM label"):
            # A separate connection also exercises SQLite's default trigger settings.
            with closing(sqlite3.connect(self.store.path)) as db:
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    db.execute(statement)
        self.store.end_run(run_id)
        bundle = self.export()
        self.assertEqual(len(read_rows(bundle, "labels")), 2)
        employers = {row["employer_id"]: row for row in read_rows(bundle, "employers")}
        self.assertEqual(employers[old]["merged_into"], successor)
        self.assertEqual(read_rows(bundle, "postings")[0]["employer_id"], old)
        backup, = (self.data / "backups").glob("*.sqlite")
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(db.execute("SELECT finished_at FROM run").fetchone()[0], START)

    def test_retention_refusal_missing_and_purged_captures(self):
        blobs = self.store.blobs
        for retention, error in (("private_email", NotImplementedError), ("restricted", ValueError),
                                 ("licensed", ValueError)):
            with self.assertRaises(error):
                blobs.put(b"do not write this capture", retention)
        self.assertFalse(list(self.data.glob("blobs/**/*.gz")))
        body = b"synthetic original response"
        sha = blobs.put(body, "public")
        self.assertEqual(blobs.put(body, "public"), sha)
        self.assertEqual(blobs.get(sha), body)
        self.store.register_capture(sha, len(body))
        run_id = self.run_id()
        poll = self.store.record_poll(run_id, self.channel(), status="ok", received=1, raw_sha=sha)
        self.post(poll, raw_sha=sha)
        self.store.end_run(run_id)
        self.assertEqual(read_rows(self.export(), "sightings")[0]["raw_locator"], "sha256:" + sha)
        self.assertEqual(blobs.purge("public", datetime.now(timezone.utc) + timedelta(days=1)), [sha])
        self.assertFalse(blobs.exists(sha))
        with self.assertRaises(FileNotFoundError):
            blobs.get(sha)
        row, = read_rows(self.export(), "sightings")
        self.assertTrue(row["raw_purged"])
        self.assertEqual(row["raw_sha"], sha)
        self.assertIsNone(row["raw_locator"])
        self.assertEqual(self.store.connection.execute("SELECT count(*) FROM posting").fetchone()[0], 1)

    def test_window_is_half_open_and_lifecycle_can_extend_outside(self):
        channel = self.channel()
        run_id = self.run_id()
        first = self.store.record_poll(run_id, channel, status="ok", received=1)
        posting = self.post(first)
        self.advance()
        second = self.store.record_poll(run_id, channel, status="ok", received=1)
        self.assertEqual(self.post(second), posting)
        self.advance()
        self.store.end_run(run_id)
        bundle = self.export(window_from=START, window_to="2026-11-18T14:00:01Z")
        self.assertEqual([row["poll_id"] for row in read_rows(bundle, "polls")], [first])
        post, = read_rows(bundle, "postings")
        self.assertEqual((post["first_seen"], post["last_seen"]), (START, "2026-11-18T14:00:01Z"))

    def test_unsafe_metadata_and_restricted_sources_stay_out_of_exports(self):
        run_id = self.run_id(trigger="Authorization-Key: secret-output")
        private = self.channel("private_email", locator="https://mail.example/subscription?token=hidden-subscriber")
        hidden = self.channel("restricted", locator="https://private.example/restricted")
        licensed = self.channel("licensed", locator="https://licensed.example/member")
        self.store.record_poll(run_id, hidden, status="skipped:restricted", received=0)
        self.store.record_poll(run_id, licensed, status="skipped:licensed", received=0)
        public = self.channel(locator="https://auth.example/auth/local-token")
        private_poll = self.store.record_poll(run_id, private, status="ok", received=1)
        public_poll = self.store.record_poll(run_id, public, status="ok", received=2)
        private_link = "https://newsletter.example/secret/embedded-secret?signature=auth-secret"
        public_link = "https://careers.example/jobs/research?gh_jid=101&tracking=unique-recipient#private-fragment"
        signed_link = "https://person:embedded-password@careers.example/job/555"
        self.post(private_poll, identity_key="url:" + private_link, url=private_link, canonical_url=private_link,
                  native_id="private-message-id", title="Reply to to-private@example.invalid",
                  employer_name_raw="To: Primary Contact", location_raw="/Users/local/path")
        self.post(public_poll, identity_key="url:" + public_link, native_id=None,
                  url=public_link, canonical_url=public_link)
        self.post(public_poll, identity_key="url:" + signed_link, native_id=None,
                  url=signed_link, canonical_url=signed_link)
        self.store.end_run(run_id)
        bundle = self.export()
        combined = b"".join(snapshot_files(bundle).values())
        for marker in ("private-user", "secret-output", "private-message-id", "embedded-secret",
                       "embedded-password", "auth-secret", "hidden-subscriber", "private-fragment",
                       "unique-recipient", "to-private@", "/Users/", "licensed.example", "private.example"):
            self.assertNotIn(marker.encode(), combined)
        self.assertEqual({row["poll_id"] for row in read_rows(bundle, "polls")}, {public_poll, private_poll})
        links = [row["url"] for row in read_rows(bundle, "sightings")]
        self.assertIn("https://careers.example/jobs/research?gh_jid=101", links)
        self.assertEqual(sum(link.startswith("https://redacted.invalid/") for link in links), 2)

    def test_parallel_sightings_share_an_identity_and_keep_both_observations(self):
        channel = self.channel()
        run_id = self.run_id()

        def write(second):
            with Store.in_data_dir(self.data) as store:
                when = self.instant + timedelta(seconds=second)
                poll = store.record_poll(run_id, channel, status="ok", received=1, started_at=when)
                return store.record_sighting(
                    poll, identity_key="greenhouse:synthetic:1", native_id="1",
                    url=GH_BOARD["url"] + "/jobs/1", canonical_url=GH_BOARD["url"] + "/jobs/1",
                    title="Synthetic role", employer_name_raw="Synthetic employer", location_raw="Unknown",
                    observed_at=when,
                )

        with ThreadPoolExecutor(max_workers=2) as workers:
            first, second = workers.map(write, (2, 1))
        self.assertEqual(first, second)
        self.instant += timedelta(seconds=3)
        self.store.end_run(run_id)
        bundle = self.export()
        row, = read_rows(bundle, "postings")
        self.assertEqual((row["first_seen"], row["last_seen"]), ("2026-11-18T14:00:01Z", "2026-11-18T14:00:02Z"))
        self.assertEqual(len(read_rows(bundle, "sightings")), 2)

    def test_rejects_in_checkout_data_and_symlinks_leaving_data_directory(self):
        checkout = self.data / "checkout"
        (checkout / ".git").mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "outside a Git checkout"):
            Store.in_data_dir(checkout / "data")
        with tempfile.TemporaryDirectory() as elsewhere:
            (self.data / "blobs").symlink_to(elsewhere, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "escapes its data directory"):
                self.store.blobs.put(b"public data", "public")
            self.assertEqual(list(Path(elsewhere).iterdir()), [])

    def test_export_requires_closed_run_and_never_overwrites_a_snapshot(self):
        run_id = self.run_id()
        self.store.record_poll(run_id, self.channel(), status="failed", received=0)
        with self.assertRaisesRegex(ValueError, "unfinished run"):
            self.export()
        self.assertEqual(list((self.data / "exports").iterdir()), [])
        self.store.end_run(run_id)
        bundle = self.export()
        with self.assertRaises(FileExistsError):
            self.export(snapshot_id=bundle.name)


class CollectorTests(StoreTestCase):
    def test_usajobs_repeated_pages_and_runs_preserve_identity_and_evidence(self):
        def collect():
            first = federal_page(101, federal_job(101), federal_job(102, "London, UK"))
            second = federal_page(101, federal_job(101, "Washington"))
            session = Session(first, second)
            run_id = self.run_id()
            stats = SourceStats()
            with patch.dict(os.environ, CREDS, clear=True):
                rows = list(collect_usajobs(self.store, run_id, session, max_pages=2, stats=stats))
            self.store.end_run(run_id)
            return rows, first, second, session

        first_rows, first, second, session = collect()
        previous = self.export()
        self.advance()
        second_rows, _, _, _ = collect()
        current = self.export()
        self.assertEqual(first_rows, second_rows)
        self.assertEqual([url for url, _ in session.calls], [FEDERAL_URL, FEDERAL_URL])
        old = {row["identity_key"]: row for row in read_rows(previous, "postings")}
        now = {row["identity_key"]: row for row in read_rows(current, "postings")}
        self.assertEqual(set(old), set(now))
        for key in old:
            self.assertEqual(old[key]["posting_id"], now[key]["posting_id"])
            self.assertEqual(old[key]["first_seen"], now[key]["first_seen"])
            self.assertLess(old[key]["last_seen"], now[key]["last_seen"])
        polls = read_rows(current, "polls")
        sightings = read_rows(current, "sightings")
        self.assertEqual(len(sightings), 6)
        self.assertEqual({row["location_raw"] for row in sightings}, {"Washington, DC", "Washington", "London, UK"})
        self.assertEqual({row["posted_at"] for row in sightings}, {"2026-11-17T14:00:00Z"})
        self.assertEqual(Counter(row["poll_id"] for row in sightings), {p["poll_id"]: 3 for p in polls})
        self.assertEqual({row["raw_sha"] for row in sightings}, {hashlib.sha256(r.content).hexdigest() for r in (first, second)})
        self.assertEqual({p["status"] for p in polls}, {"partial"})
        self.assertTrue(all(p["raw_sha"] for p in polls))
        self.assertNotIn(CREDS["USAJOBS_API_KEY"].encode(), b"".join(snapshot_files(current).values()))

    def test_usajobs_normal_entry_points_record_success_partial_failed_and_skips(self):
        environment = CREDS | {"PROSPECT_DATA_DIR": str(self.data), "PROSPECT_CODE_SHA": CODE_SHA}
        response = federal_page(1, federal_job(210))
        with patch.dict(os.environ, CREDS, clear=True):
            legacy = list(retrieve(Session(response), "usajobs"))
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(list(retrieve(Session(response), "usajobs")), legacy)
            self.assertEqual(len(list(retrieve(Session(federal_page(150, federal_job(211)), Response({}, 503)), "usajobs"))), 1)
            self.assertEqual(list(retrieve(Session(Response({}, 403)), "usajobs")), [])
            skipped, status = collect_regional(session=Session(), pause=lambda _: None)
            self.assertEqual((skipped, status["status"]), ([], "not_collected"))
        with patch.dict(os.environ, environment | {"USAJOBS_TERMS_ACCEPTED": "true"}, clear=True):
            first = federal_page(101, federal_job(220))
            repeated = federal_page(101, federal_job(220))
            regional, _ = collect_regional(session=Session(first, repeated), pause=lambda _: None)
            self.assertEqual(len(regional), 1)
        with patch.dict(os.environ, {"PROSPECT_DATA_DIR": str(self.data), "PROSPECT_CODE_SHA": CODE_SHA}, clear=True):
            self.assertEqual(list(retrieve(Session(), "usajobs")), [])
        statuses = Counter(row["status"] for row in self.store.connection.execute("SELECT status FROM poll"))
        self.assertEqual(statuses, {"ok": 1, "partial": 2, "failed": 1,
                                    "skipped:terms_unconfirmed": 1, "skipped:missing_credentials": 1})
        self.assertEqual(self.store.connection.execute("SELECT count(*) FROM sighting").fetchone()[0], 4)

    def test_greenhouse_report_dedup_remains_and_full_inventory_is_preserved(self):
        items = [greenhouse_job(11, content="A public description"),
                 greenhouse_job(11, "London, UK", content="A duplicate listing"),
                 greenhouse_job(12, "Location unresolved", content="Another posting"),
                 greenhouse_job(13, absolute_url="", content="No shareable posting URL")]
        index = Response({"jobs": items, "meta": {"total": 4}})
        board = Response({"name": "Synthetic employer"})
        def call():
            session = Session(index, board)
            transport = PublicTransport(session, pause=lambda _: None)
            rows, stats = retrieve_board(transport, GH_BOARD, detail_budget=0)
            return rows, stats, session
        with patch.dict(os.environ, {}, clear=True):
            legacy_rows, legacy_stats, legacy_session = call()
        with patch.dict(os.environ, {"PROSPECT_DATA_DIR": str(self.data), "PROSPECT_CODE_SHA": CODE_SHA}, clear=True):
            rows, stats, session = call()
            retrieve_board(PublicTransport(Session(Response({}, 503)), pause=lambda _: None), GH_BOARD, detail_budget=0)
            retrieve_board(PublicTransport(Session(), max_requests=0, pause=lambda _: None), GH_BOARD, detail_budget=0)
        self.assertEqual(stats, legacy_stats)
        self.assertEqual(session.calls, legacy_session.calls)
        self.assertEqual([row["identity"] for row in rows], [row["identity"] for row in legacy_rows])
        self.assertEqual(len(rows), 2)
        bundle = self.export(window_to=datetime.now(timezone.utc), created_at=datetime.now(timezone.utc))
        self.assertEqual({p["status"] for p in read_rows(bundle, "polls")}, {"partial", "failed", "skipped:request_budget"})
        sightings = read_rows(bundle, "sightings")
        self.assertEqual(len(sightings), 4)
        self.assertEqual(len(read_rows(bundle, "postings")), 3)
        self.assertEqual(sum(row["location_raw"] == "London, UK" for row in sightings), 1)
        self.assertTrue(next(row["url"] for row in sightings if row["native_id"] == "13").startswith("https://redacted.invalid/"))
        receipt_roles = [row["role"] for row in self.store.connection.execute("SELECT role FROM poll_capture ORDER BY ordinal")]
        self.assertEqual(receipt_roles, ["inventory", "board"])
        index_sha = hashlib.sha256(index.content).hexdigest()
        self.assertEqual(self.store.blobs.get(index_sha), index.content)
        self.assertEqual({row["raw_sha"] for row in sightings}, {index_sha})

    def test_greenhouse_detail_capture_and_report_limits_leave_evidence_intact(self):
        items = [greenhouse_job(20)] * 5000 + [greenhouse_job(21, "London, UK")]
        index = Response({"jobs": items})
        board = Response({"name": "Synthetic employer"})
        detail = Response(greenhouse_job(20, content="Details from the public posting"))
        transport = PublicTransport(Session(index, board, detail), pause=lambda _: None)
        from career_engine.store.collectors import collect_greenhouse

        run_id = self.run_id()
        rows, stats = collect_greenhouse(self.store, run_id, transport, GH_BOARD, detail_budget=1)
        self.store.end_run(run_id)
        self.assertEqual([row["source_id"] for row in rows], ["synthetic:20"])
        self.assertEqual(stats["received"], 5000)
        bundle = self.export()
        poll, = read_rows(bundle, "polls")
        self.assertEqual((poll["received"], poll["status"]), (5001, "partial"))
        sightings = read_rows(bundle, "sightings")
        self.assertEqual(len(sightings), 5001)
        self.assertIn("21", {row["native_id"] for row in sightings})
        self.assertEqual(sum(bool(row["description_sha"]) for row in sightings), 1)
        receipts = dict(self.store.connection.execute("SELECT role, raw_sha FROM poll_capture"))
        self.assertEqual(self.store.blobs.get(receipts["detail"]), detail.content)

    def test_usajobs_malformed_stream_is_captured_but_echoed_credentials_are_refused(self):
        environment = CREDS | {"PROSPECT_DATA_DIR": str(self.data), "PROSPECT_CODE_SHA": CODE_SHA,
                               "USAJOBS_TERMS_ACCEPTED": "true"}
        malformed = Response(None)
        malformed.content = b"invalid public JSON"
        echoed = federal_page(1, federal_job(515, QualificationSummary=CREDS["USAJOBS_API_KEY"]))
        with patch.dict(os.environ, environment, clear=True):
            collect_regional(session=Session(malformed), pause=lambda _: None)
            list(retrieve(Session(echoed), "usajobs"))
        polls = dict(self.store.connection.execute("SELECT status, raw_sha FROM poll"))
        self.assertEqual(self.store.blobs.get(polls["failed"]), malformed.content)
        self.assertIsNone(polls["ok"])
        self.assertFalse(self.store.blobs.exists(hashlib.sha256(echoed.content).hexdigest()))
        self.assertIsNone(self.store.connection.execute("SELECT description_sha FROM sighting").fetchone()[0])


class GoldenSnapshotTests(StoreTestCase):
    def seed(self):
        for row in read_rows(GOLDEN, "employers"):
            self.store.add_employer(row["canonical_name"], "provisional" if row["merged_into"] else row["status"],
                                    row["origin"], employer_id=row["employer_id"], aliases=row["aliases"])
        for row in read_rows(GOLDEN, "employers"):
            if row["merged_into"]:
                self.store.merge_employer(row["employer_id"], row["merged_into"])
        for row in read_rows(GOLDEN, "channels"):
            self.store.ensure_channel(**row)

        original_polls = read_rows(GOLDEN, "polls")
        purged_body = b"synthetic purged detail response"
        bodies = [f"synthetic public capture {p['poll_id']}".encode() for p in original_polls if p["raw_sha"]]
        for body in [*bodies, purged_body]:
            sha = self.store.blobs.put(body, "public")
            self.store.register_capture(sha, len(body))
        purged_sha = hashlib.sha256(purged_body).hexdigest()
        purged_path, = self.data.glob(f"blobs/public/*/{purged_sha}.gz")
        os.utime(purged_path, (1, 1))
        self.assertEqual(self.store.blobs.purge("public"), [purged_sha])

        posting_by_id = {row["posting_id"]: row for row in read_rows(GOLDEN, "postings")}
        sightings = read_rows(GOLDEN, "sightings")
        for run in read_rows(GOLDEN, "runs"):
            finished_at = run.pop("finished_at")
            self.store.begin_run(**run)
            for poll in (row for row in original_polls if row["run_id"] == run["run_id"]):
                self.store.record_poll(**poll)
                for sighting in (row for row in sightings if row["poll_id"] == poll["poll_id"]):
                    row = {key: value for key, value in sighting.items()
                           if key not in ("channel_id", "raw_locator", "raw_purged")}
                    posting = posting_by_id[row["posting_id"]]
                    self.store.record_sighting(**row, identity_key=posting["identity_key"], employer_id=posting["employer_id"])
            self.store.end_run(run["run_id"], finished_at=finished_at)
        for posting in posting_by_id.values():
            if posting["closed_observed_at"]:
                self.store.observe_closure(posting["posting_id"], posting["closed_observed_at"])
        for label in read_rows(GOLDEN, "labels"):
            self.store.add_label(**label)

    def test_fixture_store_reproduces_every_byte(self):
        self.seed()
        expected = json.loads((GOLDEN / "manifest.json").read_bytes())
        metadata = dict(window_from=expected["window"]["from"], window_to=expected["window"]["to"],
                        created_at=expected["created_at"], snapshot_id=expected["snapshot_id"],
                        code_sha=expected["prospect_code_sha"])
        first = export_snapshot(self.store, self.data / "exports", **metadata)
        second = export_snapshot(self.store, self.data / "repeat", **metadata)
        self.assertEqual(snapshot_files(first), snapshot_files(GOLDEN))
        self.assertEqual(snapshot_files(second), snapshot_files(GOLDEN))

    def test_cli_writes_a_snapshot_and_manifest_for_completed_data(self):
        self.instant = datetime.now(timezone.utc) - timedelta(minutes=5)
        run_id = self.run_id()
        poll = self.store.record_poll(run_id, self.channel(), status="ok", received=1)
        self.post(poll)
        self.store.end_run(run_id)
        expected = self.export()

        result = subprocess.run([
            sys.executable, "-m", "career_engine.export", "--data-dir", str(self.data),
            "--out", str(self.data / "cli"), "--code-sha", CODE_SHA,
        ], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        created = Path(result.stdout.strip())
        manifest = json.loads((created / "manifest.json").read_bytes())
        self.assertEqual(manifest["contract_version"], "prospect-export/1")
        self.assertNotEqual(created.name, expected.name)
        self.assertEqual(manifest["files"], json.loads((expected / "manifest.json").read_bytes())["files"])
        for name, info in manifest["files"].items():
            data = (created / name).read_bytes()
            self.assertEqual(info, dict(rows=len(data.splitlines()), sha256=hashlib.sha256(data).hexdigest()))



if __name__ == "__main__":
    unittest.main()
