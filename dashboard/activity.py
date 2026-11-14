"""Read the pilot's public progress metadata from an explicitly configured store."""
import csv
import json
import os
import re
import stat
from pathlib import Path

from career_engine.store.privacy import AUTH_PATH, Projection

MAX_FILE_BYTES = 2 * 1024 * 1024
ARMS = ("seeded", "cold")
PROVIDERS = ("greenhouse", "lever", "jazzhr")
ROUTES = ("robots", "catalog", "index_count", "index", "sitemap", "metadata", "inventory", "detail")
REASONS = {
    "interrupted", "integrity_storage_or_unexpected_failure", "network_error",
    "stream_error", "request_timeout", "time_budget", "byte_budget", "request_budget",
    "host_stopped_after_denial", "access_challenge_not_bypassed",
    "terms_unverified_or_restricted", "robots_disallowed_or_unavailable",
    "robots_disallowed", "robots_unsupported", "stricter_crawl_delay_skip",
    "response_too_large", "invalid_json", "invalid_schema", "incomplete_response",
    "unsafe_endpoint", "unsafe_redirect", "private_page_state",
    "unexpected_content_type", "unexpected_content_encoding", "invalid_content_length",
    "invalid_encoding", "duplicate_request", "adapter_or_parser_error",
    "provider_total_differs_from_returned_inventory", "repeated_page",
}
UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z")


def _read_file(directory, name):
    """Read only bounded regular files at the pilot's fixed output names."""
    try:
        parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        finally:
            os.close(parent)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise OSError("Unexpected pilot file")
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("Pilot file exceeds viewer limit")
    return data


def _object(data):
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Expected a pilot object")
    return value


def _complete_lines(data):
    if not data or data.endswith(b"\n"):
        return data or b"", False
    return data.rpartition(b"\n")[0], True


def _reason(value, privacy):
    if isinstance(value, str) and not privacy.unsafe(value) and (
        value in REASONS or re.fullmatch(r"http_[1-5][0-9]{2}", value)
    ):
        return value
    return "Details withheld"


def _board(arm, key, privacy):
    if arm not in ARMS or not isinstance(key, str):
        return None
    provider, _, tenant = key.partition(":")
    if (provider in PROVIDERS and len(tenant) <= 100 and privacy.tenant_is_shareable(tenant)
            and not AUTH_PATH.fullmatch(tenant) and not privacy.unsafe(key)):
        return arm, provider, tenant
    return None


def _timestamp(value, privacy):
    if isinstance(value, str) and UTC.fullmatch(value) and not privacy.unsafe(value):
        return value
    return None


def _board_result(event, privacy):
    status = event.get("status")
    if event.get("dispatched") is False:
        return "Skipped", _reason(event.get("reason"), privacy)
    if isinstance(status, str) and status.startswith("skipped:"):
        return "Skipped", _reason(status.removeprefix("skipped:"), privacy)
    if status == "ok":
        return "Completed", None
    if status in ("failed", "partial"):
        errors = event.get("errors")
        reason = _reason(errors[0], privacy) if isinstance(errors, list) and errors else None
        return status.title(), reason
    return "Outcome unavailable", None


def _request_result(event, privacy):
    http = event.get("status")
    http = http if type(http) is int and 100 <= http <= 599 else None
    if event.get("error"):
        return "Failed", http, _reason(event["error"], privacy)
    if http is None:
        return "Outcome unavailable", None, None
    if http >= 400:
        return "Failed", http, None
    return ("Redirected" if 300 <= http < 400 else "Completed"), http, None


def activity(data_dir):
    """Project pilot files into display rows. Never return source payloads."""
    directory = Path(data_dir).expanduser() / "run"
    privacy = Projection()
    files, notices = {}, []
    # A final manifest is published after the journal. Read it first so a newly
    # observed manifest cannot be paired with a pre-finalization journal read.
    for name in ("run-manifest.json", "cohort.csv", "operations.jsonl"):
        try:
            files[name] = _read_file(directory, name)
        except (OSError, ValueError):
            files[name] = None
            notices.append(f"Unable to read {name}.")

    boards = {}
    cohort, partial_cohort = _complete_lines(files["cohort.csv"])
    if cohort:
        try:
            for row in csv.DictReader(cohort.decode("utf-8").splitlines(), strict=True):
                key = _board(row.get("arm"), row.get("board_key"), privacy)
                if key:
                    boards[key] = dict(state="Planned", reason=None, dispatched=None, inventory_attempted=None)
        except (UnicodeError, csv.Error):
            notices.append("Some planned sources could not be read.")
    if partial_cohort:
        notices.append("The source plan is still being written.")

    journal, partial_tail = _complete_lines(files["operations.jsonl"])
    if partial_tail:
        notices.append("Waiting for the last journal entry to be written.")
    requests, started, finished = {}, set(), set()
    stopped, blocked, policy_stop = None, None, None
    invalid_entry = False
    for line in journal.splitlines():
        try:
            event = _object(line)
        except (ValueError, RecursionError):
            invalid_entry = True
            continue
        phase = event.get("phase")
        arm = event.get("arm")
        if phase == "arm_started" and arm in ARMS:
            started.add(arm)
        elif phase == "arm_finished" and arm in ARMS:
            finished.add(arm)
        elif phase in ("board_dispatched", "board_finished"):
            key = _board(arm, event.get("board_key"), privacy)
            if key:
                state, reason = (_board_result(event, privacy) if phase == "board_finished"
                                 else ("Dispatched", None))
                dispatched = True if phase == "board_dispatched" else event.get("dispatched")
                attempted = event.get("attempted") if phase == "board_finished" else None
                boards[key] = dict(
                    state=state, reason=reason,
                    dispatched=dispatched if type(dispatched) is bool else None,
                    inventory_attempted=attempted if type(attempted) is bool else None,
                )
        elif phase in ("attempt", "result"):
            number = event.get("attempt")
            stage = event.get("stage")
            if type(number) is not int or not 0 < number <= 10000 or stage not in (*ARMS, "discovery"):
                continue
            route = event.get("route")
            key = _board(stage, event.get("board_key"), privacy)
            if stage in ARMS and key is None:
                continue
            state, http, reason = (_request_result(event, privacy) if phase == "result"
                                   else ("Attempted", None, None))
            requests[number] = dict(
                attempt=number, stage=stage, provider=key[1] if key else None,
                source=key[2] if key else None,
                route=route if route in ROUTES else None, state=state, http_status=http,
                started_at=_timestamp(event.get("observed_at"), privacy), reason=reason,
            )
        elif phase == "stopped":
            stopped = _reason(event.get("reason"), privacy)
        elif phase == "export_blocked":
            blocked = _reason(event.get("reason"), privacy)
        elif phase == "policy_stop":
            policy_stop = _reason(event.get("reason"), privacy)
    if invalid_entry:
        notices.append("Some journal entries could not be read.")

    snapshot = None
    if files["run-manifest.json"] is not None:
        try:
            manifest = _object(files["run-manifest.json"])
            snapshot_id = manifest.get("snapshot_id")
            digest = manifest.get("snapshot_manifest_sha256")
            if (not isinstance(snapshot_id, str)
                    or not re.fullmatch(r"[0-7][0-9A-HJKMNP-TV-Z]{25}", snapshot_id)
                    or privacy.unsafe(snapshot_id) or not isinstance(digest, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                raise ValueError("Invalid pilot manifest")
            snapshot = snapshot_id
            if manifest.get("stop_reason"):
                stopped = _reason(manifest["stop_reason"], privacy)
        except (ValueError, RecursionError):
            notices.append("The final run manifest is unreadable or incomplete.")

    if snapshot:
        status = "Final export published"
    elif stopped == "interrupted":
        status = "Interrupted"
    elif blocked:
        status = "Stopped · export blocked"
    elif stopped:
        status = "Stopped"
    elif policy_stop:
        status = "Policy stop recorded · awaiting final status"
    elif finished == set(ARMS):
        status = "Collection finished · awaiting final export"
    elif started or boards or requests:
        status = "Activity recorded · awaiting final status"
    else:
        status = "No pilot activity recorded"

    terminal = bool(snapshot or stopped or blocked)
    source_rows = []
    for (arm, provider, source), outcome in boards.items():
        state = outcome["state"]
        if state == "Dispatched":
            state = ("Dispatched · no outcome recorded" if terminal or arm in finished
                     else "Dispatched · in progress (last recorded)")
        elif state == "Planned" and (terminal or arm in finished):
            state = "Planned · no dispatch recorded"
        source_rows.append(dict(arm=arm, provider=provider, source=source,
                                **{**outcome, "state": state}))
    for request in requests.values():
        if request["state"] == "Attempted":
            request["state"] = ("Attempted · no result recorded" if terminal
                                or request["stage"] in finished else "Attempted · awaiting result")
    request_rows = sorted(requests.values(), key=lambda row: row["attempt"], reverse=True)
    return dict(status=status, reason=blocked or policy_stop or stopped, snapshot=snapshot, notices=notices,
                sources=source_rows, requests=request_rows,
                journal_present=files["operations.jsonl"] is not None)
