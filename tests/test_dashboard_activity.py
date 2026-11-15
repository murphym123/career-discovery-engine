"""Offline replay of the pilot files read by Live activity."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from career_engine.store import Store
from dashboard.activity import activity
from dashboard.queries import collection_totals, open_store
import test_dashboard as fixture_data
from test_dashboard import FRACTION, WHOLE, channel, run, sighting


class ActivityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.directory = self.data / "run"
        self.directory.mkdir()
        self.journal = self.directory / "operations.jsonl"

    def append(self, phase, **fields):
        with self.journal.open("a") as stream:
            stream.write(json.dumps(dict(fields, phase=phase)) + "\n")

    def plan(self):
        (self.directory / "cohort.csv").write_text(
            "arm,slot,provider,board_key,board_url,remote_control\n"
            "seeded,1,lever,lever:civic,https://example.org/?token=private,False\n"
            "seeded,2,greenhouse,greenhouse:institute,https://example.org/,False\n"
            "cold,1,jazzhr,jazzhr:transit,https://example.org/,False\n"
        )

    def test_plan_dispatch_attempt_commit_and_finalize(self):
        self.plan()
        with Store(self.data / "prospect.sqlite") as writer:
            batch = run(writer, "seeded")
            public = channel(writer, "lever")
            with open_store(self.data) as reader:
                self.assertEqual(collection_totals(reader), dict(received=0, sightings=0, posting_ids=0))
            self.assertEqual([row["state"] for row in activity(self.data)["sources"]], ["Planned"] * 3)

            board = dict(arm="seeded", board_key="lever:civic")
            request = dict(attempt=1, stage="seeded", route="inventory", board_key="lever:civic")
            self.append("arm_started", arm="seeded")
            self.append("board_dispatched", **board)
            self.append("attempt", **request)
            progress = activity(self.data)
            self.assertEqual(progress["sources"][0]["state"], "Dispatched · in progress (last recorded)")
            self.assertEqual(progress["requests"][0]["state"], "Attempted · awaiting result")

            self.append("result", **request, status=200)
            poll = writer.record_poll(batch, public, status="partial", received=3, unrepresented=1,
                                      started_at=WHOLE)
            sighting(writer, poll, "First observation", WHOLE)
            sighting(writer, poll, "Repeat observation", FRACTION)
            self.append("board_finished", **board, status="partial", dispatched=True, attempted=True,
                        errors=["provider_total_differs_from_returned_inventory"])
            self.append("board_finished", arm="seeded", board_key="greenhouse:institute",
                        dispatched=False, reason="time_budget")
            self.append("arm_finished", arm="seeded")
            self.append("arm_finished", arm="cold")
            progress = activity(self.data)
            self.assertEqual(progress["status"], "Collection finished · awaiting final export")
            self.assertEqual([row["state"] for row in progress["sources"]],
                             ["Partial", "Skipped", "Planned · no dispatch recorded"])
            self.assertEqual(progress["requests"][0]["state"], "Completed")
            self.assertTrue(progress["sources"][0]["inventory_attempted"])
            self.assertFalse(progress["sources"][1]["dispatched"])
            with open_store(self.data) as reader:
                self.assertEqual(collection_totals(reader), dict(received=3, sightings=2, posting_ids=1))

        manifest = self.directory / "run-manifest.json"
        manifest.write_text('{"snapshot_id":')
        progress = activity(self.data)
        self.assertIsNone(progress["snapshot"])
        self.assertTrue(progress["notices"])
        snapshot_id = "0" * 26
        staged = manifest.with_suffix(".tmp")
        staged.write_text(json.dumps(dict(snapshot_id=snapshot_id, snapshot_manifest_sha256="b" * 64)))
        staged.replace(manifest)
        progress = activity(self.data)
        self.assertEqual(progress["status"], "Final export published")
        self.assertEqual(progress["snapshot"], snapshot_id)

    def test_partial_and_bad_journal_lines_and_interrupted_run(self):
        self.append("board_dispatched", arm="seeded", board_key="lever:civic")
        self.append("attempt", attempt=1, stage="seeded", board_key="lever:civic")
        with self.journal.open("ab") as stream:
            stream.write(b'bad line\n{"phase":"stopped",')
        progress = activity(self.data)
        self.assertEqual(len(progress["notices"]), 2)
        self.assertIn("awaiting result", progress["requests"][0]["state"])
        with self.journal.open("ab") as stream:
            stream.write(b'"reason":"interrupted"}\n')
        progress = activity(self.data)
        self.assertEqual(progress["status"], "Interrupted")
        self.assertEqual(progress["sources"][0]["state"], "Dispatched · no outcome recorded")
        self.assertEqual(progress["requests"][0]["state"], "Attempted · no result recorded")

    def test_only_safe_fields_and_known_reasons_are_returned(self):
        self.plan()
        self.append("result", attempt=1, stage="seeded", board_key="lever:civic",
                    route="inventory", status=429, error="http_429",
                    url="https://example.org/?token=private", body="PRIVATE_BODY",
                    headers={"Authorization": "SECRET_HEADER"}, raw_sha="SECRET_HASH")
        self.append("result", attempt=2, stage="discovery", route="PRIVATE_ROUTE",
                    error="free form PRIVATE_ERROR", observed_at="PRIVATE_TIMESTAMP")
        self.append("board_dispatched", arm="seeded", board_key="lever:fixture-secret")
        self.append("board_dispatched", arm="seeded", board_key="lever:private@example.org")
        self.append("board_dispatched", arm="seeded", board_key="restricted:PRIVATE_SOURCE")
        self.append("export_blocked", reason="private_page_state", payload="PRIVATE_PAYLOAD")
        with patch.dict("os.environ", {"FIXTURE_API_KEY": "fixture-secret"}):
            progress = activity(self.data)
        self.assertNotRegex(str(progress), "PRIVATE_|SECRET_|fixture-secret|token=|@")
        self.assertEqual(progress["status"], "Stopped · export blocked")
        self.assertEqual(progress["reason"], "private_page_state")
        self.assertEqual(progress["requests"][1]["http_status"], 429)
        self.assertEqual(progress["requests"][1]["state"], "Failed")
        self.assertEqual(progress["requests"][0]["reason"], "Details withheld")

    def test_policy_stop_waits_for_final_status(self):
        self.append("policy_stop", reason="unsafe_endpoint")
        progress = activity(self.data)
        self.assertEqual(progress["status"], "Policy stop recorded · awaiting final status")
        self.assertEqual(progress["reason"], "unsafe_endpoint")

    def test_missing_and_symlinked_files(self):
        self.assertFalse(activity(self.data)["journal_present"])
        self.assertEqual(activity(self.data)["status"], "No pilot activity recorded")
        other = self.data / "private.jsonl"
        other.write_text('{"phase":"stopped","reason":"interrupted"}\n')
        self.journal.symlink_to(other)
        progress = activity(self.data)
        self.assertFalse(progress["journal_present"])
        self.assertEqual(progress["status"], "No pilot activity recorded")
        self.assertEqual(progress["notices"], ["Unable to read operations.jsonl."])

    def test_totals_exclude_private_and_reference_sightings(self):
        fixture = fixture_data.DashboardTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.populate()
        with open_store(fixture.data_dir) as reader:
            self.assertEqual(collection_totals(reader), dict(received=3, sightings=2, posting_ids=1))
