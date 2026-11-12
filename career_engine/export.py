"""Write an immutable prospect-export/1 snapshot from the local SQLite store."""
import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

from career_engine.store import Store, git_sha, timestamp
from career_engine.store.blobs import outside_git
from career_engine.store.privacy import Projection

VERSION = "prospect-export/1"
DRAFT_VERSION = "prospect-export/1.1"
ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
LABEL_FIELDS = "subject, subject_id, dimension, value, method, classifier_version, created_at"
LABEL_ORDER = "subject, subject_id, dimension, created_at, classifier_version, method, value"
SELECTIONS = {
    "poll": """
        SELECT poll_id FROM poll JOIN channel USING (channel_id)
        WHERE channel.retention_class IN ('public', 'private_email')
          AND rtrim(started_at, 'Z') >= rtrim(?, 'Z') AND rtrim(started_at, 'Z') < rtrim(?, 'Z')
    """,
    "channel": "SELECT DISTINCT channel_id FROM poll JOIN export_poll USING (poll_id)",
    "run": "SELECT DISTINCT run_id FROM poll JOIN export_poll USING (poll_id)",
    "sighting": "SELECT sighting_id FROM sighting JOIN export_poll USING (poll_id)",
    "posting": "SELECT DISTINCT posting_id FROM sighting JOIN export_sighting USING (sighting_id)",
    "employer": """
        WITH RECURSIVE chosen(employer_id) AS (
            SELECT employer_id FROM channel JOIN export_channel USING (channel_id) WHERE employer_id IS NOT NULL
            UNION SELECT employer_id FROM posting JOIN export_posting USING (posting_id) WHERE employer_id IS NOT NULL
            UNION SELECT merged_into FROM employer JOIN chosen USING (employer_id) WHERE merged_into IS NOT NULL
        ) SELECT employer_id FROM chosen
    """,
}

# Explicit field lists keep store-only fields, raw captures, and future schema
# additions out of the wire format.
FIELDS = {
    "run": "run_id, started_at, finished_at, code_sha, config_sha, trigger, arm",
    "channel": "channel_id, employer_id, kind, platform, locator, retention_class, evidence_role, verification_state",
    "poll": """poll_id, run_id, channel_id, started_at, status, declared_total, received, raw_sha,
               unrepresented, capture_withheld, poll_accounting.poll_id IS NOT NULL AS accounted""",
    "employer": "employer_id, canonical_name, status, origin, merged_into",
    "posting": "posting_id, employer_id, identity_key, first_seen, last_seen, closed_observed_at",
    "sighting": """sighting_id, posting_id, poll_id, channel_id, native_id, url, canonical_url,
                   title, employer_name_raw, location_raw, posted_at, closing_at, observed_at, description_sha, raw_sha""",
}


def _ulid(created_at):
    milliseconds = int(datetime.fromisoformat(created_at).timestamp() * 1000)
    if not 0 <= milliseconds < (1 << 48):
        raise ValueError("Snapshot creation time is outside the ULID range")
    value = (milliseconds << 80) | secrets.randbits(80)
    return "".join(ULID_ALPHABET[(value >> shift) & 31] for shift in range(125, -1, -5))


def _json_line(obj):
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n").encode("utf-8")


def _write_rows(directory, filename, rows):
    sha = hashlib.sha256()
    count = 0
    with (directory / filename).open("xb") as output:
        for row in rows:
            line = _json_line(row)
            output.write(line)
            sha.update(line)
            count += 1
        output.flush()
        os.fsync(output.fileno())
    return {"rows": count, "sha256": sha.hexdigest()}


def _select_rows(db, window_from, window_to):
    for entity, selection in SELECTIONS.items():
        params = (window_from, window_to) if entity == "poll" else ()
        db.execute(f"CREATE TEMP TABLE export_{entity} AS {selection}", params)
    if db.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("Prospect store has a broken foreign key")
    records = {
        entity: [dict(row) for row in db.execute(f"""
            SELECT {fields} FROM {entity} JOIN export_{entity} USING ({entity}_id)
            {"LEFT JOIN poll_accounting USING (poll_id)" if entity == "poll" else ""}
            ORDER BY {entity}_id
        """)]
        for entity, fields in FIELDS.items()
    }
    if any(row["finished_at"] is None for row in records["run"]):
        raise ValueError("Snapshot window contains polls from an unfinished run")
    aliases = {}
    for row in db.execute("""
        SELECT employer_id, kind, value FROM employer_alias
        JOIN export_employer USING (employer_id) ORDER BY employer_id, kind, value
    """):
        aliases.setdefault(row["employer_id"], []).append(dict(kind=row["kind"], value=row["value"]))
    subjects = " UNION ALL ".join(f"SELECT '{name}', {name}_id FROM export_{name}" for name in FIELDS)
    records["label"] = [dict(row) for row in db.execute(f"""
        WITH included(entity, id) AS ({subjects})
        SELECT {LABEL_FIELDS} FROM label JOIN included ON subject = entity AND subject_id = id
        ORDER BY {LABEL_ORDER}
    """)]
    for entity in SELECTIONS:
        db.execute(f"DROP TABLE export_{entity}")
    return records, aliases


def _safe_projection(store, records, aliases, privacy):
    for entity in FIELDS:
        for row in records[entity]:
            privacy.identifier(row[f"{entity}_id"])

    channels = {row["channel_id"]: row for row in records["channel"]}
    for row in records["channel"]:
        locator = None
        if row["retention_class"] == "public":
            locator = privacy.url(row["locator"])
        row["locator"] = locator or f"urn:prospect:channel:{row['channel_id']}"
        row["platform"] = privacy.native(row["platform"])
        for field in ("kind", "evidence_role", "verification_state"):
            row[field] = privacy.text(row[field])

    for row in records["run"]:
        for field in ("trigger", "arm"):
            row[field] = privacy.text(row[field])

    for row in records["employer"]:
        for field in ("canonical_name", "origin"):
            row[field] = privacy.text(row[field])
        row["aliases"] = sorted(
            ({"kind": privacy.text(alias["kind"]), "value": privacy.text(alias["value"])}
             for alias in aliases.get(row["employer_id"], ())),
            key=lambda alias: (alias["kind"], alias["value"]),
        )

    for row in records["poll"]:
        if channels[row["channel_id"]]["retention_class"] != "public":
            row["raw_sha"] = None
        row["status"] = "skipped:redacted" if privacy.unsafe(row["status"]) else row["status"]

    available = {}
    native_keys = set()
    public_posting_urls = {}
    for row in records["sighting"]:
        channel = channels[row["channel_id"]]
        row["url"], row["canonical_url"] = privacy.posting_urls(row)
        if row["canonical_url"] != privacy.placeholder(row["posting_id"]):
            public_posting_urls.setdefault(row["posting_id"], row["canonical_url"])
        for field in ("title", "employer_name_raw", "location_raw"):
            row[field] = privacy.text(row[field])
        row["raw_locator"] = None
        if channel["retention_class"] == "private_email":
            for field in ("native_id", "description_sha", "raw_sha"):
                row[field] = None
            continue
        row["native_id"] = privacy.native(row["native_id"])
        if row["native_id"]:
            native_keys.add((row["posting_id"], channel["platform"], row["native_id"]))
        sha = row["raw_sha"]
        if sha:
            if sha not in available:
                available[sha] = store.blobs.exists(sha)
            if available[sha]:
                row["raw_locator"] = f"sha256:{sha}"
            else:
                row["raw_purged"] = True

    for row in records["posting"]:
        identity = row["identity_key"]
        platform, _, remainder = identity.partition(":")
        tenant, _, native_id = remainder.partition(":")
        if (privacy.native(platform) and privacy.tenant_is_shareable(tenant) and privacy.native(native_id)
                and (row["posting_id"], platform, native_id) in native_keys):
            continue
        url = public_posting_urls.get(row["posting_id"])
        if url and platform == "url":
            url = privacy.url(remainder) or url
        row["identity_key"] = "url:" + (url or privacy.placeholder(row["posting_id"]))

    for row in records["label"]:
        for field in ("dimension", "value", "method", "classifier_version"):
            row[field] = privacy.text(row[field])
    order = LABEL_ORDER.split(", ")
    records["label"].sort(key=lambda row: tuple(row[field] for field in order))


def _account_for_polls(records, *, draft_1_1):
    counts = Counter(sighting["poll_id"] for sighting in records["sighting"])
    retention = {channel["channel_id"]: channel["retention_class"] for channel in records["channel"]}
    for poll in records["poll"]:
        poll_id = poll["poll_id"]
        received = poll["received"]
        unrepresented = poll["unrepresented"]
        withheld = poll["capture_withheld"]
        public = retention[poll["channel_id"]] == "public"
        if counts[poll_id] + unrepresented != received:
            raise ValueError(f"Poll {poll_id} has unreconciled records")
        if unrepresented and (not poll["accounted"] or poll["status"] not in ("partial", "failed")):
            raise ValueError(f"Poll {poll_id} has unverified unrepresented records")
        if withheld is not None and (withheld != "secret_echo" or not public or poll["raw_sha"] is not None):
            raise ValueError(f"Poll {poll_id} has an invalid capture withholding record")
        if public and poll["status"] in ("ok", "partial") and received and poll["raw_sha"] is None and not withheld:
            raise ValueError(f"Poll {poll_id} is missing an inventory capture without an audited reason")
        if not draft_1_1:
            if unrepresented or withheld:
                raise ValueError(f"Poll {poll_id} requires proposed {DRAFT_VERSION} accounting. "
                                 "No approved snapshot was published")
            del poll["unrepresented"], poll["capture_withheld"]
        del poll["accounted"]


def _classifier_versions(labels):
    versions = {"geo": "none", "in_scope": "none"}
    for label in sorted(labels, key=lambda row: (row["created_at"].removesuffix("Z"), row["classifier_version"])):
        versions[label["dimension"]] = label["classifier_version"]
    return versions


def export_snapshot(store, out, *, window_from="1970-01-01T00:00:00Z", window_to=None,
                    market_id="dc", code_sha=None, created_at=None, snapshot_id=None,
                    classifier_versions=None, draft_1_1=False):
    """Return the new snapshot directory. For reproduction supply the original metadata."""
    created_at = timestamp(created_at) if created_at is not None else store.now()
    start = timestamp(window_from)
    end = timestamp(window_to) if window_to is not None else created_at
    if start[:-1] >= end[:-1] or end[:-1] > created_at[:-1]:
        raise ValueError("Expected a nonempty window ending by snapshot creation")
    snapshot_id = snapshot_id or _ulid(created_at)
    if not re.fullmatch(r"[0-7][0-9A-HJKMNP-TV-Z]{25}", snapshot_id):
        raise ValueError("Invalid snapshot ULID")
    sha = git_sha(code_sha)
    privacy = Projection()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", market_id) or privacy.unsafe(market_id):
        raise ValueError("Invalid or unsafe market identifier")
    directory = outside_git(out)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = directory / snapshot_id
    if target.exists():
        raise FileExistsError("Snapshot already exists")
    staged = Path(tempfile.mkdtemp(prefix=".snapshot-", dir=directory))
    try:
        with store.blobs.locked(), store.transaction(write=False) as db:
            records, aliases = _select_rows(db, start, end)
            _safe_projection(store, records, aliases, privacy)
            _account_for_polls(records, draft_1_1=draft_1_1)
        versions = _classifier_versions(records["label"]) if classifier_versions is None else {
            privacy.text(dimension): privacy.text(version) for dimension, version in classifier_versions.items()
        }
        files = {
            f"{entity}s.jsonl": _write_rows(staged, f"{entity}s.jsonl", rows)
            for entity, rows in records.items()
        }
        manifest = {
            "contract_version": DRAFT_VERSION if draft_1_1 else VERSION, "snapshot_id": snapshot_id, "created_at": created_at,
            "market_id": market_id, "prospect_code_sha": sha, "store_schema_version": store.schema_version,
            "classifier_versions": versions, "window": {"from": start, "to": end},
            "run_ids": [row["run_id"] for row in records["run"]], "files": files,
        }
        with (staged / "manifest.json").open("xb") as output:
            output.write(_json_line(manifest))
            output.flush()
            os.fsync(output.fileno())
        if target.exists():
            raise FileExistsError("Snapshot already exists")
        os.rename(staged, target)
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=os.environ.get("PROSPECT_DATA_DIR"),
                        help="Directory containing prospect.sqlite (default: PROSPECT_DATA_DIR)")
    parser.add_argument("--out", help="Parent directory for new snapshots (default: DATA_DIR/exports)")
    parser.add_argument("--from", dest="window_from", default="1970-01-01T00:00:00Z", help="Inclusive poll start")
    parser.add_argument("--to", dest="window_to", help="Exclusive poll start (default: snapshot creation time)")
    parser.add_argument("--market", default="dc", help="Market identifier in the manifest")
    parser.add_argument("--code-sha", help="Source Git commit (default: PROSPECT_CODE_SHA or checkout HEAD)")
    parser.add_argument("--draft-1-1", action="store_true",
                        help="Write the proposed 1.1 format for synthetic compatibility testing only")
    args = parser.parse_args()
    if not args.data_dir:
        parser.error("Supply --data-dir or set PROSPECT_DATA_DIR")
    if args.draft_1_1:
        print(f"Warning: {DRAFT_VERSION} is unapproved and cannot be used for scoring.", file=sys.stderr)
    try:
        with Store.in_data_dir(args.data_dir, create=False) as store:
            snapshot = export_snapshot(
                store, args.out or store.path.parent / "exports", window_from=args.window_from,
                window_to=args.window_to, market_id=args.market, code_sha=args.code_sha, draft_1_1=args.draft_1_1,
            )
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, f"Export failed: {error}\n")
    print(snapshot)


if __name__ == "__main__":
    main()
