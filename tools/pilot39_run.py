"""Private run inputs, durable orchestration, and offline interruption inspection."""
import argparse
from collections import Counter
import csv
import json
import os
from pathlib import Path
import platform
import re
import signal
import sqlite3
import stat
import subprocess
from contextlib import closing
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests

from career_engine.ats_registry import discover_commoncrawl, empty_registry
from career_engine.export import export_snapshot
from career_engine.markets import DC_MARKET
from career_engine.public_sources import discover_jazz_sitemap
from career_engine.relabel import relabel
from career_engine.store import Store, git_sha, timestamp
from career_engine.store.blobs import BlobStore, digest, outside_git
from career_engine.store.privacy import Projection
from tools import pilot39 as pilot


# Template permissions apply only in the cold arm, after the normal pinned
# board and exact route checks. JazzHR cold permission covers index only.
COLD_SCOPES = {
    "greenhouse": {
        ("boards-api.greenhouse.io", "/v1/boards/{tenant}"),
        ("boards-api.greenhouse.io", "/v1/boards/{tenant}/jobs"),
    },
    "lever": {("api.lever.co", "/v0/postings/{tenant}"), ("jobs.lever.co", "/{tenant}")},
    "jazzhr": {("{tenant}.applytojob.com", "/apply")},
}


def seeded_scope(host, path):
    routes = {
        "boards-api.greenhouse.io": r"/v1/boards/[A-Za-z0-9._%-]+(?:/jobs)?",
        "api.lever.co": r"/v0/postings/[A-Za-z0-9._%-]+",
        "jobs.lever.co": r"/[A-Za-z0-9._%-]+",
    }
    if host in routes:
        url = f"https://{host}{path}"
        return re.fullmatch(routes[host], path) and Projection().url(url) == url
    return re.fullmatch(r"[a-z0-9-]+\.applytojob\.com", host) and path in ("/apply", "/apply/{posting}")


BOARD_RETENTION = "diagnostic_run_only"


class Terms:
    """Allow only reviewed endpoint scopes for this one pass."""

    def __init__(self, entries, reviewed_at):
        if not isinstance(entries, list):
            raise ValueError("Expected a list of reviewed terms entries")
        self.entries = {}
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {
                "host", "path", "arm", "documentation", "terms", "checked_at", "reviewer",
                "disposition", "retention",
            }:
                raise ValueError("Invalid terms entry")
            if any(not isinstance(value, str) for value in entry.values()):
                raise ValueError("Invalid terms entry")
            host, path, arm = (entry[key] for key in ("host", "path", "arm"))
            route = arm, host, path
            supported = (
                (arm == "cold" and any((host, path) in scopes for scopes in COLD_SCOPES.values()))
                or (arm == "discovery" and (
                    host == pilot.INDEX_HOST and (
                        path == "/collinfo.json" or re.fullmatch(r"/CC-MAIN-(?:[0-9]{4}-[0-9]{2}|\{collection\})-index", path)
                    ) or host == pilot.FEED_HOST and path in ("/feeds/google/xml/0", "/feeds/google/xml/1")
                ))
                or (arm == "seeded" and seeded_scope(host, path))
            )
            retention = "digest_only" if arm == "discovery" else BOARD_RETENTION
            if (not supported or route in self.entries
                    or entry["disposition"] not in {"allowed", "disallowed", "robots_disallowed", "unverified"}
                    or entry["retention"] != retention
                    or not re.fullmatch(r"[A-Za-z0-9 ._-]{1,80}", entry["reviewer"])
                    or any(entry[field] and Projection().url(entry[field]) != entry[field]
                           for field in ("documentation", "terms"))
                    or not (entry["documentation"] or entry["terms"])
                    or timestamp(entry["checked_at"])[:-1] > reviewed_at[:-1]):
                raise ValueError("Invalid or unreviewed terms scope")
            self.entries[route] = entry
        self.routes = {route for route, entry in self.entries.items() if entry["disposition"] == "allowed"}

    def reason(self, host, path, arm):
        key = (arm, host, path)
        if arm == "discovery" and host == pilot.INDEX_HOST and path.endswith("-index") and key not in self.entries:
            key = (arm, host, "/CC-MAIN-{collection}-index")
        disposition = self.entries.get(key, {}).get("disposition")
        return {"disallowed": pilot.TERMS_DENIED, "robots_disallowed": "robots_disallowed"}.get(
            disposition, pilot.POLICY
        )

    def __call__(self, host, path, arm, board):
        if (arm, host, path) in self.entries:
            return (arm, host, path) in self.routes
        if arm == "discovery" and host == pilot.INDEX_HOST and re.fullmatch(
            r"/CC-MAIN-[0-9]{4}-[0-9]{2}-index", path
        ):
            return (arm, host, "/CC-MAIN-{collection}-index") in self.routes
        if arm != "cold" or board is None:
            return False
        tenant = urlsplit(board["url"]).hostname.split(".")[0] if board["provider"] == "jazzhr" else (
            urlsplit(board["url"]).path[1:]
        )
        return any(
            (arm, template_host, template_path) in self.routes
            and (host, path) == (
                template_host.replace("{tenant}", tenant), template_path.replace("{tenant}", tenant),
            )
            for template_host, template_path in COLD_SCOPES[board["provider"]]
        )


def private_input(path, limit):
    path = outside_git(path)
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or info.st_size > limit):
        raise ValueError("Use an owner-only regular pilot input outside Git")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Pilot input exceeds size limit")
    return data


def checked_config(raw):
    config = json.loads(raw, object_pairs_hook=unique_keys)
    if not isinstance(config, dict) or set(config) != {"code_sha", "terms_get_attempts", "terms"}:
        raise ValueError("Invalid pilot configuration")
    operator_attempts = config["terms_get_attempts"]
    if type(operator_attempts) is not int or not 0 <= operator_attempts <= pilot.STAGE_LIMITS["terms"]:
        raise ValueError("Invalid number of operator terms GETs")
    code_sha = git_sha(config["code_sha"])
    if config["code_sha"] != code_sha:
        raise ValueError("Expected a pinned source commit")
    return code_sha, operator_attempts, Terms(config["terms"], timestamp())


def unique_keys(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate pilot configuration key")
    return result


def verify_checkout(code_sha):
    repo = Path(__file__).resolve().parents[1]

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()

    if (git("rev-parse", "HEAD") != code_sha or git("rev-parse", "--abbrev-ref", "HEAD") != "HEAD"
            or git("status", "--porcelain", "--untracked-files=all")):
        raise ValueError("Use a clean detached checkout of the approved merged main revision")


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def write_bytes(path, value):
    with path.open("xb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Journal:
    def __init__(self, stream):
        self.stream = stream

    def __call__(self, phase, event):
        event = dict(event, phase=phase)
        # Posting slugs/native IDs and query values are unnecessary for
        # reconciliation. The collectors still get the internal final URL.
        if phase != "source_access":
            event.pop("url", None)
        elif event.get("route") == "detail":
            event["url"] = f"https://{urlsplit(event['url']).hostname}/apply/{{posting}}"
        if phase == "source_access" and event.get("rule"):
            if pilot.unsafe_body(event["rule"]) or pilot.contains_configured_secret(
                event["rule"], Projection().secrets
            ):
                event["rule"] = "[withheld]"
        self.stream.write(json.dumps(event, sort_keys=True, allow_nan=False) + "\n")
        self.stream.flush()
        os.fsync(self.stream.fileno())


def collect(data, boards, cohort_bytes, config_bytes, code_sha, operator_attempts, terms):
    """Own a new directory for one pass. A failed or stopped directory is never reused."""
    data = outside_git(data)
    parent = data.parent.stat()
    if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise ValueError("Use an owner-only pilot parent directory")
    previous_umask = os.umask(0o077)
    try:
        data.mkdir(mode=0o700, exist_ok=False)
        sync_directory(data.parent)
        run = data / "run"
        run.mkdir()
        write_bytes(run / "cohort.txt", cohort_bytes)
        write_bytes(run / "config.json", config_bytes)
        (run / "cohort.txt").chmod(0o400)
        (run / "config.json").chmod(0o400)
        config_sha = pilot.sha(config_bytes)
        provenance = dict(code_sha=code_sha, config_sha256=config_sha, cohort_sha256=pilot.COHORT_SHA,
                          cohort_file_sha256=pilot.sha(cohort_bytes), development_evidence=True,
                          stage_limits=pilot.STAGE_LIMITS, total_limit=pilot.TOTAL_LIMIT,
                          byte_limit=pilot.BYTE_LIMIT, repeat_enabled=False,
                          raw_retention=BOARD_RETENTION,
                          python=platform.python_version(), sqlite=sqlite3.sqlite_version,
                          requests=requests.__version__, system=platform.system(),
                          runner_sha256={
                              name: pilot.sha(Path(__file__).with_name(name).read_bytes())
                              for name in ("pilot39.py", "pilot39_run.py", "pilot39_http.py", "pilot39_robots.py")
                          })
        write_json(run / "provenance.json", provenance)
        with (run / "operations.jsonl").open("x", encoding="utf-8") as stream:
            sync_directory(run)
            sync_directory(data)
            journal = Journal(stream)
            transport = pilot.LivePilotTransport(terms, journal)
            transport.budget.counts["terms"] = operator_attempts
            try:
                return execute(data, run, boards, transport, journal, provenance)
            except KeyboardInterrupt:
                journal("stopped", {"reason": "interrupted"})
                raise
            except Exception:
                journal("stopped", {"reason": "integrity_storage_or_unexpected_failure"})
                raise
            finally:
                try:
                    transport.close()
                finally:
                    # Covers stopped or interrupted runs as well as successful ones.
                    purge_diagnostic_bodies(data)
    finally:
        os.umask(previous_umask)


def purge_diagnostic_bodies(data):
    data = outside_git(data)
    provenance = json.loads((data / "run" / "provenance.json").read_text())
    if provenance.get("raw_retention") != BOARD_RETENTION:
        raise ValueError("Expected an issue #44 diagnostic directory")
    return BlobStore(data).purge("public", older_than=datetime.max.replace(tzinfo=timezone.utc))


def execute(data, run, seeded, transport, journal, provenance):
    window_from = timestamp()
    registry = empty_registry()
    index_template = "/CC-MAIN-{collection}-index"
    if (isinstance(transport.terms, Terms)
            and transport.terms.reason(pilot.INDEX_HOST, index_template, "discovery") == "robots_disallowed"):
        queries = [dict(kind="index_preflight", status="robots_disallowed", new_boards=0,
                        url=f"https://{pilot.INDEX_HOST}{index_template}")]
    else:
        queries = discover_commoncrawl(transport, registry, query_budget=2, include_icims=False)
    feeds = discover_jazz_sitemap(transport, registry, page_budget=2, market=DC_MARKET)
    cold, candidates = pilot.select_cold(registry, seeded)
    with (run / "candidate-ledger.jsonl").open("x", encoding="utf-8") as stream:
        candidate_journal = Journal(stream)
        for candidate in candidates:
            candidate_journal("candidate", candidate)
    planned = []
    for arm, boards in (("seeded", seeded), ("cold", cold)):
        for slot, board in enumerate(boards):
            control = arm == "seeded" and slot in {10, 13}
            planned.append(dict(arm=arm, slot=slot + 1, provider=board["provider"], board_key=board["key"],
                                board_url=board["url"], remote_control=control))
    with (run / "cohort.csv").open("x", encoding="utf-8") as stream:
        fields = ("arm", "slot", "provider", "board_key", "board_url", "remote_control")
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(planned)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(run)

    ledger = []
    with Store.in_data_dir(data) as store:
        for arm, boards in (("seeded", seeded), ("cold", cold)):
            run_id = store.begin_run(code_sha=provenance["code_sha"], config_sha=provenance["config_sha256"],
                                     trigger="issue39", arm=arm)
            journal("arm_started", dict(arm=arm, run_id=run_id))
            transport.run_id = run_id
            for slot, board in enumerate(boards):
                transport.stage_for_board(arm, board, int(slot in pilot.DETAIL_SLOTS))
                entry = dict(arm=arm, slot=slot + 1, provider=board["provider"], board_key=board["key"],
                             run_id=run_id, dispatched=False, attempted=False, allowed=False, retrieved=False)
                if reason := transport.global_stop_reason():
                    entry["reason"] = reason
                else:
                    entry["dispatched"] = True
                    journal("board_dispatched", entry)
                    entry.update(poll_board(store, run_id, transport, board))
                journal("board_finished", entry)
                ledger.append(entry)
            store.end_run(run_id)
            journal("arm_finished", dict(arm=arm, run_id=run_id))
        reason = transport.global_stop_reason()
        if reason in (pilot.PRIVATE, pilot.REDIRECT, "unsafe_endpoint"):
            journal("export_blocked", {"reason": reason})
            raise ValueError("Privacy or endpoint policy stop blocks export")
        relabel(store, "geo", "gazetteer-dc/1")
        # Materialize labels while raw evidence is available. Export after
        # deletion so the immutable snapshot describes the evidence it retains.
        for row in ledger:
            if "poll_id" not in row:
                continue
            row["geography_sightings"] = dict(store.connection.execute("""
                SELECT value, count(*) FROM label JOIN sighting ON subject_id = sighting_id
                WHERE subject = 'sighting' AND dimension = 'geo' AND poll_id = ?
                GROUP BY value
            """, (row["poll_id"],)))
            row["uncertain_geography_sightings"] = sum(
                row["geography_sightings"].get(value, 0) for value in ("unknown", "remote_eligibility_unknown")
            )
        purge_diagnostic_bodies(data)
        for row in ledger:
            for capture in row.get("captures", ()):
                capture["retained"] = False
        window_to = store.now()
        backup = store.backup()
        snapshot = export_snapshot(store, data / "exports", code_sha=provenance["code_sha"],
                                   window_from=window_from, window_to=window_to)
    manifest = dict(
        provenance, window={"from": window_from, "to": window_to}, discovery=safe_discovery(queries + feeds),
        stop_reason=transport.halted or None, operators_terms_attempts=transport.budget.counts["terms"],
        stage_attempts=dict(transport.budget.counts), bytes=transport.budget.bytes,
        raw_bodies_purged=True,
        cold_discovered=dict(candidates=len(candidates), new=sum(not row["seed_overlap"] for row in candidates),
                             selected=len(cold)),
        stopped_hosts=transport.blocked, snapshot_id=snapshot.name, post_replay_backup=backup.name,
        snapshot_manifest_sha256=pilot.sha((snapshot / "manifest.json").read_bytes()),
        board_counts=[
            dict(arm=arm, provider=provider, planned=len(group),
                 dispatched=sum(row["dispatched"] for row in group),
                 allowed=sum(row["allowed"] for row in group),
                 retrieved=sum(row["retrieved"] for row in group),
                 confirmed_empty=sum(row.get("confirmed_empty", False) for row in group),
                 uncertain_geography_sightings=sum(row.get("uncertain_geography_sightings", 0) for row in group),
                 potential_false_exclusions=dict(Counter(
                     row.get("reason", "unknown") for row in group
                     if row.get("potential_false_exclusion", not row["attempted"])
                 )),
                 skipped_by_reason=dict(Counter(
                     row.get("reason", "unknown") for row in group if not row["attempted"]
                 )),
                 inventory_attempts=sum(row["attempted"] for row in group),
                 outcomes=dict(Counter(row.get("status", "unattempted").split(":")[0] for row in group)))
            for arm in ("seeded", "cold") for provider in pilot.COLLECTORS
            if (group := [row for row in ledger if row["arm"] == arm and row["provider"] == provider])
        ],
    )
    write_json(run / "board-polls.json", ledger)
    staged_manifest = run / ".run-manifest.tmp"
    write_json(staged_manifest, manifest)
    sync_directory(snapshot)
    sync_directory(snapshot.parent)
    staged_manifest.rename(run / "run-manifest.json")
    sync_directory(run)
    return snapshot


def safe_discovery(entries):
    fields = ("kind", "provider", "pattern", "collection", "page", "new_boards",
              "index_lines", "board_URLs", "complete_URL_entries", "capped")
    return [{**{key: row[key] for key in fields if key in row},
             **({"url": row["url"]} if row.get("kind") == "index_preflight" else {}),
             "status": safe_reason(row["status"])} for row in entries]


def safe_reason(value):
    # Error messages from adapters or parsers can interpolate response values.
    # Only their fixed reason codes can enter a journal.
    allowed = {
        "ok", "no_public_board_URLs_in_index_sample", "empty_or_schema_changed",
        "network_error", "stream_error", "request_timeout", "time_budget", "byte_budget", "request_budget",
        "robots_disallowed_or_unavailable", "robots_unsupported", "stricter_crawl_delay_skip",
        "robots_disallowed", "sitemap_not_advertised", "host_stopped_after_denial",
        "response_too_large", "content_list_response_too_large", "repeated_page", "invalid_json",
        "invalid_schema", "catalog_schema", "no_public_index_rows_or_schema_changed",
        "public_description_section_unavailable_or_title_mismatch", "posting_ID_mismatch",
        "public_JobPosting_title_mismatch", "public_description_empty",
        "unsafe_endpoint", "unsafe_public_job_url", "board_index_redirected", "archive_fallback_disabled",
        "unexpected_content_encoding", "unexpected_content_type", "invalid_encoding",
        "invalid_content_length", "incomplete_response", "unreadable_public_html",
        "provider_total_differs_from_returned_inventory", "duplicate_request",
        pilot.PRIVATE, pilot.CHALLENGE, pilot.POLICY, pilot.TERMS_DENIED, pilot.REDIRECT,
    }
    if value.startswith("board_name_"):
        return "board_name_" + safe_reason(value.removeprefix("board_name_"))
    if value in allowed or re.fullmatch(r"http_[1-5][0-9]{2}", value):
        return value
    return "adapter_or_parser_error"


def poll_board(store, run_id, transport, board):
    before = len(transport.requests)
    before_access = len(transport.access)
    args = (store, run_id, transport, board)
    if board["provider"] == "jazzhr":
        _, stats = pilot.collect_jazzhr(*args, detail_budget=transport.detail_limit, market=DC_MARKET)
    else:
        _, stats = pilot.COLLECTORS[board["provider"]](*args, max_pages=3, detail_budget=0, market=DC_MARKET)
    poll = store.connection.execute("""
        SELECT p.poll_id, p.channel_id, p.status, p.received, p.declared_total, p.unrepresented, p.raw_sha,
               (SELECT count(DISTINCT posting_id) FROM sighting WHERE poll_id = p.poll_id) AS unique_postings
        FROM poll p JOIN channel c USING (channel_id)
        WHERE p.run_id = ? AND c.channel_key = ?
    """, (run_id, board["key"])).fetchone()
    if poll is None:
        raise RuntimeError("Collector did not commit a poll")
    attempts = transport.requests[before:]
    receipts = store.connection.execute("""
        SELECT disposition, raw_sha, sightings
        FROM poll_inventory WHERE poll_id = ? ORDER BY ordinal
    """, (poll["poll_id"],))
    captures = store.connection.execute("""
        SELECT role, raw_sha, byte_count FROM poll_capture JOIN raw_capture USING(raw_sha)
        WHERE poll_id = ? ORDER BY ordinal
    """, (poll["poll_id"],))
    inventory = stats["inventory"]
    reasons = [safe_reason(reason) for reason in stats["errors"]]
    rollup = Counter(event["route"] for event in attempts)
    accepted_inventory = [event for event in attempts if event["route"] == "inventory"
                          and event.get("status") == 200 and not event.get("error")]
    access = [event for event in transport.access[before_access:] if event["route"] == "inventory"]
    access_reasons = list(dict.fromkeys(event.get("cause", event["decision"]) for event in access
                                       if event["decision"] != "allowed"))
    sightings = poll["received"] - poll["unrepresented"]
    confirmed_empty = poll["status"] == "ok" and inventory["observed_available_items"] == 0
    return dict(
        dict(poll), allowed=any(event["decision"] == "allowed" for event in access),
        retrieved=bool(sightings) or confirmed_empty, confirmed_empty=confirmed_empty,
        reason="confirmed_empty" if confirmed_empty else (access_reasons or reasons or (
            ["unrepresented_rows"] if poll["unrepresented"] else
            ["inventory_completeness_unverified"] if inventory["metadata_complete"] is not True
            and poll["status"] == "ok" else [poll["status"]]
        ))[0],
        access_reasons=access_reasons,
        coverage_outcome=("confirmed_empty" if confirmed_empty else poll["status"] if sightings else
                          (access_reasons or reasons or [poll["status"]])[0]),
        potential_false_exclusion=bool(poll["status"] != "ok" or poll["unrepresented"]
                                       or inventory["metadata_complete"] is not True and not confirmed_empty), **{key: inventory[key] for key in (
            "observed_available_items", "metadata_complete", "descriptions_observed",
        )}, attempted=bool(rollup["inventory"]), preflight_only=bool(attempts) and not rollup["inventory"],
        request_count=len(attempts), request_counts=dict(rollup),
        valid_inventory_responses=len(accepted_inventory), sightings=sightings,
        response_bytes=sum(event.get("bytes", 0) for event in attempts),
        inventory_receipts=[dict(receipt) for receipt in receipts],
        captures=[dict(capture, retained=store.blobs.exists(capture["raw_sha"])) for capture in captures],
        errors=reasons,
        warnings=[safe_reason(warning) for warning in stats["warnings"]],
        capped=stats["capped"], detail_cap=transport.detail_limit,
    )


def inspect_run(data):
    """Reconcile an interrupted pass without changing its store or issuing requests."""
    data = outside_git(data)
    run = data / "run"
    attempted, results, dispatched, reported = set(), set(), set(), set()
    truncated_tail = False
    with (run / "operations.jsonl").open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n"):
                truncated_tail = True
                break
            event = json.loads(line)
            if event["phase"] == "attempt":
                attempted.add(event["attempt"])
            elif event["phase"] == "result":
                results.add(event["attempt"])
            elif event["phase"] == "board_dispatched":
                dispatched.add((event["run_id"], event["board_key"]))
            elif event["phase"] == "board_finished" and event.get("poll_id"):
                reported.add(event["poll_id"])
    runs, committed = [], []
    db = data / "prospect.sqlite"
    if db.is_file():
        with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            runs = [dict(row) for row in connection.execute(
                "SELECT run_id, arm, started_at, finished_at FROM run ORDER BY started_at"
            )]
            committed = [dict(row) for row in connection.execute("""
                SELECT run_id, channel_key AS board_key, poll_id, status, received, unrepresented
                FROM poll JOIN channel USING(channel_id) ORDER BY poll.started_at
            """)]
    through_store = {(row["run_id"], row["board_key"]) for row in committed}
    return dict(
        finalized=(run / "run-manifest.json").is_file(), incomplete_journal_tail=truncated_tail,
        requests_without_result=sorted(attempted - results), requests_journaled=len(attempted),
        runs=runs, committed_polls=committed,
        polls_without_ledger_result=[row["poll_id"] for row in committed if row["poll_id"] not in reported],
        dispatches_without_poll=sorted(dispatched - through_store),
    )


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    precheck = commands.add_parser("precheck")
    collect_parser = commands.add_parser("collect")
    for command in (precheck, collect_parser):
        command.add_argument("--cohort", required=True, type=Path,
                             help="Private file with the 24 canonical board URLs in order")
    precheck.add_argument("--config", type=Path)
    collect_parser.add_argument("--config", required=True, type=Path)
    collect_parser.add_argument("--owner-approved-config-sha256", required=True)
    inspect_parser = commands.add_parser("inspect")
    inspect_parser.add_argument("--data-dir", required=True, type=Path)
    purge_parser = commands.add_parser("purge-raw", help="Remove diagnostic response bodies after an unclean stop")
    purge_parser.add_argument("--data-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "purge-raw":
        print(json.dumps({"purged": len(purge_diagnostic_bodies(args.data_dir))}))
        return
    if args.command == "inspect":
        print(json.dumps(inspect_run(args.data_dir), sort_keys=True, indent=2))
        return

    cohort = private_input(args.cohort, 16384)
    boards = pilot.check_cohort(cohort.decode("utf-8").splitlines())
    raw = private_input(args.config, 65536) if args.config else None
    checked = checked_config(raw) if raw is not None else None
    if args.command == "precheck":
        print(json.dumps(dict(cohort_sha256=pilot.COHORT_SHA, board_count=len(boards),
                              platforms=Counter(b["provider"] for b in boards),
                              config_sha256=pilot.sha(raw) if checked else None)))
        return
    if (os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS") or platform.system() != "Darwin"
            or digest(args.owner_approved_config_sha256) != pilot.sha(raw)):
        parser.error("Collect requires the approved configuration digest on the owner's Mac")
    data_dir = os.environ.get("PROSPECT_DATA_DIR")
    if not data_dir or not Path(data_dir).is_absolute():
        parser.error("Set PROSPECT_DATA_DIR to a new absolute private run directory")
    verify_checkout(checked[0])

    def stop(*_):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, stop)
    try:
        print(collect(data_dir, boards, cohort, raw, *checked))
    finally:
        signal.signal(signal.SIGTERM, previous)
