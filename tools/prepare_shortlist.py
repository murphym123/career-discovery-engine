"""Join offline triage evidence or validate the private issue #73 shortlist.

Run as python -m tools.prepare_shortlist. The shortlist contract uses version 1,
nullable location/work arrangement/compensation, and unchecked availability only.
"""
import argparse
import json
import sqlite3
import stat
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

from career_engine.compact_triage import CLASSES, strict_json
from career_engine.immediate_review import is_private, open_private, public_link, sha256
from career_engine.store.blobs import outside_git
from career_engine.store.privacy import Projection

SCHEMA_VERSION = 1
AUDIT_SEED = "prospect-issue-73-negative-audit-v1"
AUDIT_SIZE = 20


def load_corpus(directory, filename):
    manifest = strict_json((directory / "manifest.json").read_bytes())
    raw = (directory / filename).read_bytes()
    if sha256(raw) != manifest["corpus"]["sha256"]:
        raise ValueError("Saved corpus checksum mismatch")
    rows = {}
    for line in raw.splitlines():
        row = strict_json(line)
        review_id = row["review_id"]
        if review_id in rows:
            raise ValueError("Duplicate review ID in saved corpus")
        rows[review_id] = row
    if len(rows) != manifest["corpus"]["rows"]:
        raise ValueError("Saved corpus row count mismatch")
    return manifest, rows


def join_record(record, compact, accepted=None):
    if (record["review_id"] != compact["review_id"]
            or record["record_sha256"] != compact["record_sha256"]):
        raise ValueError("Original and compact record disagree")
    if accepted is not None and (
        accepted["review_id"] != compact["review_id"]
        or accepted["record_sha256"] != compact["record_sha256"]
        or accepted["source_sha256"] != compact["source_excerpt"]["source_sha256"]
        or accepted["source_tier"] != compact["source_tier"]
        or accepted["classification"] not in CLASSES
    ):
        raise ValueError("Checkpoint and compact record disagree")
    preferred = [v for v in record["variants"]
                 if v["variant_sha256"] == record["preferred_variant_sha256"]]
    if len(preferred) != 1:
        raise ValueError("Expected one preferred variant")
    return {
        "review_id": record["review_id"], "checkpoint": accepted, "compact": compact,
        "record": record, "preferred": preferred[0]["data"],
    }


def negative_sample(accepted):
    unlikely = (r for r in accepted if r["classification"] == "unlikely")
    return sorted(unlikely, key=lambda r: sha256(
        f"{AUDIT_SEED}:{r['review_id']}".encode("utf-8")
    ))[:AUDIT_SIZE]


def make_packet(export_dir, compact_dir, checkpoint, extra_ids=()):
    original, records = load_corpus(export_dir, "records.jsonl")
    compact_manifest, compact = load_corpus(compact_dir, "compact-records.jsonl")
    if original["corpus"]["sha256"] != compact_manifest["export_corpus_sha256"]:
        raise ValueError("Compact corpus was built from a different export")
    with closing(sqlite3.connect(checkpoint.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        accepted = [dict(row) for row in db.execute("SELECT * FROM results ORDER BY review_id")]
    joined = {r["review_id"]: join_record(records[r["review_id"]], compact[r["review_id"]], r)
              for r in accepted}
    audit = negative_sample(accepted)
    extra_ids = tuple(extra_ids)
    if len(set(extra_ids)) != len(extra_ids) or set(extra_ids) & joined.keys():
        raise ValueError("Supplemental IDs must be distinct and outside the checkpoint")
    return {
        "original_corpus_sha256": original["corpus"]["sha256"],
        "compact_corpus_sha256": compact_manifest["corpus"]["sha256"],
        "sources": original["sources"],
        "audit_seed": AUDIT_SEED,
        "review": [joined[r["review_id"]] for r in accepted if r["classification"] != "unlikely"],
        "negative_sample": [joined[r["review_id"]] for r in audit],
        "supplemental": [join_record(records[rid], compact[rid]) for rid in extra_ids],
    }


def validate_shortlist(data):
    def has_text(value):
        return isinstance(value, str) and bool(value.strip())

    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or (
        data["schema_version"] != SCHEMA_VERSION
    ):
        raise ValueError("Expected shortlist schema version 1")
    if (not has_text(data.get("generated_at"))
            or datetime.fromisoformat(data["generated_at"].replace("Z", "+00:00")).utcoffset() is None):
        raise ValueError("Expected a timezone-aware generated_at")
    jobs = data.get("jobs")
    if not isinstance(jobs, list) or len(jobs) > 10:
        raise ValueError("Expected a jobs array of up to 10 items")
    seen = set()
    for job in jobs:
        if not isinstance(job, dict) or not all(has_text(job.get(k)) for k in (
            "review_id", "title", "employer", "official_url",
        )):
            raise ValueError("Job requires a review ID, title, employer and official URL")
        if job["review_id"] in seen:
            raise ValueError("Duplicate shortlist review ID")
        seen.add(job["review_id"])
        if not public_link(job["official_url"], Projection()):
            raise ValueError("Expected a safe public posting URL")
        for key in ("location", "work_arrangement", "compensation_text"):
            if key not in job or (job[key] is not None and not has_text(job[key])):
                raise ValueError(f"Expected {key} to be text or null")
        for key in ("fit_reasons", "gaps_to_verify"):
            items = job.get(key)
            if not isinstance(items, list) or not items or not all(map(has_text, items)):
                raise ValueError(f"Expected nonempty text in {key}")
        if job.get("review_priority") not in ("high", "medium"):
            raise ValueError("Expected high or medium review priority")
        status = job.get("availability_status")
        checked_at = job.get("availability_checked_at")
        if status == "not_yet_verified":
            if checked_at is not None:
                raise ValueError("Unchecked availability must have a null checked time")
        elif status in ("verified_current", "no_longer_open"):
            if not has_text(checked_at):
                raise ValueError("Verified availability requires a checked time")
            try:
                parsed = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
            except ValueError as error:
                raise ValueError("Expected a valid verification timestamp") from error
            if parsed.utcoffset() is None:
                raise ValueError("Verification timestamp must include a timezone")
        else:
            raise ValueError("Unknown availability status")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    packet = sub.add_parser("packet", help="Write full offline evidence to a new private file")
    for flag in ("export-dir", "compact-dir", "checkpoint", "output"):
        packet.add_argument(f"--{flag}", required=True, type=Path)
    packet.add_argument("--extra-id", action="append", default=[])
    validate = sub.add_parser("validate", help="Validate the private offline shortlist contract")
    validate.add_argument("shortlist", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "validate":
            path = outside_git(args.shortlist)
            if not is_private(path, stat.S_ISREG):
                raise ValueError("Shortlist must be a private regular file")
            validate_shortlist(strict_json(path.read_bytes()))
        else:
            directory = outside_git(args.output.parent)
            if not is_private(directory, stat.S_ISDIR):
                raise ValueError("Output directory must be private")
            data = make_packet(args.export_dir, args.compact_dir, args.checkpoint, args.extra_id)
            with open_private(directory / args.output.name) as out:
                out.write((json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        print("Shortlist operation failed. Check source integrity, schema and private paths.",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
