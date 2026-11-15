import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from career_engine.immediate_review import sha256
from tools.prepare_shortlist import make_packet, negative_sample, validate_shortlist


def corpus(directory, name, rows, **extra):
    directory.mkdir()
    raw = "".join(json.dumps(row) + "\n" for row in rows).encode()
    (directory / name).write_bytes(raw)
    digest = sha256(raw)
    (directory / "manifest.json").write_text(json.dumps({
        "corpus": {"rows": len(rows), "sha256": digest}, **extra,
    }), encoding="utf-8")
    return digest


def shortlist(**changes):
    job = {
        "review_id": "review-example", "title": "Research Assistant", "employer": "Example",
        "location": None, "work_arrangement": None, "compensation_text": None,
        "official_url": "https://example.org/jobs/42", "fit_reasons": ["Summarizes hearings"],
        "gaps_to_verify": ["Confirm worksite"], "review_priority": "high",
        "availability_status": "not_yet_verified", "availability_checked_at": None,
    }
    return {"schema_version": 1, "generated_at": "2026-01-02T03:04:05Z",
            "jobs": [{**job, **changes}]}


class PrepareShortlistTests(unittest.TestCase):
    def test_packet_joins_preferred_evidence_and_checks_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            full, compact, checkpoint = base / "full", base / "compact", base / "checkpoint.db"
            review_id, record_hash, source_hash = "review-example", "a" * 64, "b" * 64
            description = "Introduction " + "x" * 500 + " Bachelor's degree required."
            full_hash = corpus(full, "records.jsonl", [{
                "review_id": review_id, "record_sha256": record_hash,
                "preferred_variant_sha256": "c" * 64,
                "variants": [
                    {"variant_sha256": "d" * 64, "data": {"description": "Other"}},
                    {"variant_sha256": "c" * 64, "data": {"description": description}},
                ],
            }], sources=[])
            corpus(compact, "compact-records.jsonl", [{
                "review_id": review_id, "record_sha256": record_hash, "source_tier": "TARGETED",
                "source_excerpt": {"source_sha256": source_hash}, "snippet": description[:500],
            }], export_corpus_sha256=full_hash)
            with closing(sqlite3.connect(checkpoint)) as db, db:
                db.execute("CREATE TABLE results (review_id, record_sha256, source_sha256, "
                           "source_tier, classification)")
                db.execute("INSERT INTO results VALUES (?, ?, ?, ?, ?)",
                           (review_id, record_hash, source_hash, "TARGETED", "cant_tell"))
            packet = make_packet(full, compact, checkpoint)
            self.assertEqual(packet["review"][0]["preferred"]["description"], description)
            self.assertEqual(packet["review"][0]["checkpoint"]["classification"], "cant_tell")
            self.assertEqual(len(packet["review"][0]["record"]["variants"]), 2)
            self.assertEqual(packet["negative_sample"], [])
            with closing(sqlite3.connect(checkpoint)) as db, db:
                db.execute("UPDATE results SET record_sha256 = ?", ("e" * 64,))
            with self.assertRaisesRegex(ValueError, "Checkpoint and compact"):
                make_packet(full, compact, checkpoint)

    def test_negative_sample_is_stable_unique_and_only_unlikely(self):
        rows = [{"review_id": str(i), "classification": "unlikely" if i % 2 else "plausible"}
                for i in range(60)]
        sample = negative_sample(rows)
        self.assertEqual(sample, negative_sample(reversed(rows)))
        self.assertEqual(len({row["review_id"] for row in sample}), 20)
        self.assertTrue(all(row["classification"] == "unlikely" for row in sample))

    def test_shortlist_accepts_nullable_unknowns_and_unchecked_availability(self):
        validate_shortlist(shortlist())

    def test_shortlist_accepts_verified_availability_with_timestamp(self):
        validate_shortlist(shortlist(availability_status="verified_current",
                                    availability_checked_at="2026-11-15T08:00:00Z"))

    def test_shortlist_rejects_unsafe_urls_and_unsubstantiated_availability(self):
        for changes in (
            {"official_url": "javascript:confirm(1)"},
            {"official_url": "https://example.org/jobs/42?token=synthetic"},
            {"availability_status": "verified_current"},
            {"availability_checked_at": "2026-01-02T03:04:05Z"},
            {"availability_status": "verified_current", "availability_checked_at": "tomorrow"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_shortlist(shortlist(**changes))

    def test_shortlist_rejects_duplicates_and_missing_fit_evidence(self):
        data = shortlist()
        data["jobs"] *= 2
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            validate_shortlist(data)
        with self.assertRaisesRegex(ValueError, "fit_reasons"):
            validate_shortlist(shortlist(fit_reasons=[]))
