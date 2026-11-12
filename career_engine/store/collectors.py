"""Opt-in persistence at the existing USAJOBS and Greenhouse collector boundaries."""
import hashlib
import json
import os
import re
from contextlib import contextmanager

from career_engine.store import Store, git_sha, timestamp
from career_engine.store.privacy import Projection

USAJOBS_CHANNEL = dict(kind="api", platform="usajobs", locator="https://www.usajobs.gov/",
                       channel_key="usajobs:dc-radius-35")
SKIP_REASONS = {
    "request_budget", "byte_budget", "time_budget", "host_stopped_after_denial",
}


@contextmanager
def configured_run(config):
    """Leave existing callers alone unless they configure a Prospect data directory."""
    data_dir = os.environ.get("PROSPECT_DATA_DIR")
    if not data_dir:
        yield None
        return
    # The caller supplies only its public knobs, never headers, URLs or environment.
    config_sha = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    with Store.in_data_dir(data_dir) as store:
        run_id = store.begin_run(code_sha=git_sha(), config_sha=config_sha)
        try:
            yield store, run_id
        finally:
            store.end_run(run_id)


def _source_date(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        return timestamp(value)
    except ValueError:
        return None


def _total(value):
    try:
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            return None
        total = int(value)
        return total if total >= 0 else None
    except ValueError:
        return None


class _ObservationBatch:
    def __init__(self, store, run_id, channel, *, private_values=()):
        self.store = store
        self.run_id = run_id
        self.channel_id = store.ensure_channel(**channel, retention_class="public",
                                              evidence_role="discovery", verification_state="verified")
        self.started_at = store.now()
        self.receipts = []
        self.observations = []
        self.details = {}
        self.current_inventory = None
        self.declared_total = None
        self.received = 0
        self.privacy = Projection()
        self.private_values = tuple(value for value in private_values if value)

    def capture(self, body, role):
        if not isinstance(body, bytes):
            return
        if any(value.encode() in body for value in self.private_values):
            return  # The API may authenticate the request, but only public response bodies belong here.
        sha = self.store.blobs.put(body, "public")
        self.receipts.append((sha, role, len(body), self.store.now()))
        if role == "inventory":
            self.current_inventory = sha

    def usajobs_response(self, body):
        self.current_inventory = None
        self.capture(body, "inventory")

    def usajobs_page(self, total, received):
        self.declared_total = _total(total)
        self.received += received

    def observe(self, row, identity_key, native_id):
        self.observations.append((row.copy(), identity_key, native_id, self.current_inventory, self.store.now()))

    def usajobs_sighting(self, row):
        native_id = row["source_id"]
        if native_id == "None" or self.privacy.native(native_id) is None or native_id in self.private_values:
            native_id = None
        url = str(row["url"] or "")
        if native_id is None:
            if not url or not row["title"]:
                return
            canonical = self.privacy.url(url)
            identity = "url:" + canonical if canonical else "unshareable:" + hashlib.sha256(url.encode()).hexdigest()
        else:
            identity = "usajobs:usajobs:" + native_id
        self.observe(row, identity, native_id)

    def greenhouse_inventory(self, board, items, company):
        from career_engine.ats import normalize_posting

        self.received = len(items)
        for item in items:
            try:
                row = normalize_posting(board, item, company)
            except (ValueError, TypeError, AttributeError):
                # A provider ID still identifies the posting when its link is
                # missing or fails the legacy URL checks.
                native_id = str(item.get("id", "")) if isinstance(item, dict) else ""
                if native_id.isdigit():
                    self.observe(
                        dict(title=item.get("title"), company=item.get("company_name") or company,
                             location=item.get("location", {}).get("name") if isinstance(item.get("location"), dict)
                             else item.get("location"), url="", posted_at=item.get("first_published"),
                             closing_at=item.get("application_deadline")),
                        f"{board['key']}:{native_id}", native_id,
                    )
                continue
            self.observe(row, row["identity"], row["identity"].rsplit(":", 1)[-1])

    def _without_request_secrets(self, value):
        return "" if any(secret in value for secret in self.private_values) else value

    def greenhouse_detail(self, row):
        self.details[row["identity"]] = row

    def commit(self, status):
        raw_sha = next((sha for sha, role, _, _ in self.receipts if role == "inventory"), None)
        raw_sha = raw_sha or next((receipt[0] for receipt in self.receipts), None)
        with self.store.transaction():
            for sha, _, byte_count, captured_at in self.receipts:
                self.store.register_capture(sha, byte_count, captured_at=captured_at)
            poll_id = self.store.record_poll(
                self.run_id, self.channel_id, started_at=self.started_at, status=status,
                received=self.received, declared_total=self.declared_total, raw_sha=raw_sha,
                captures=((sha, role) for sha, role, _, _ in self.receipts),
                unrepresented=self.received - len(self.observations),
            )
            for row, identity_key, native_id, capture_sha, observed_at in self.observations:
                detail = self.details.pop(identity_key, None)
                description_row = detail if detail and detail.get("description") else row
                description = description_row.get("description")
                description_sha = None
                if description and not any(value in description for value in self.private_values):
                    description_sha = description_row.get("description_sha256") or hashlib.sha256(description.encode()).hexdigest()
                canonical_url = str(row["url"] or "")
                observed_url = str(row.get("original_posting_url") or canonical_url)
                self.store.record_sighting(
                    poll_id, identity_key=identity_key, native_id=native_id,
                    url=self._without_request_secrets(observed_url),
                    canonical_url=self._without_request_secrets(canonical_url),
                    title=self.privacy.text(str(row.get("title") or "")),
                    employer_name_raw=self.privacy.text(str(row.get("company") or "")),
                    location_raw=self.privacy.text(str(row.get("location") or "")),
                    posted_at=_source_date(row.get("posted_at")), closing_at=_source_date(row.get("closing_at")),
                    observed_at=observed_at, description_sha=description_sha, raw_sha=capture_sha,
                )


def collect_usajobs(store, run_id, session, max_pages=3, stats=None):
    """Yield the existing collector output while retaining each occurrence and its page."""
    from career_engine.regional_usajobs import BoundedSession
    from career_engine.sources import REQUIREMENTS, SourceStats, retrieve

    stats = SourceStats() if stats is None else stats
    batch = _ObservationBatch(store, run_id, USAJOBS_CHANNEL,
                              private_values=(os.getenv(key) for key in REQUIREMENTS["usajobs"]))
    streaming = isinstance(session, BoundedSession)
    previous_capture = session.capture_body if streaming else None
    if streaming:
        session.capture_body = batch.usajobs_response
    finished = False
    try:
        yield from retrieve(session, "usajobs", max_pages, stats, _evidence=batch)
        finished = True
    finally:
        if streaming:
            session.capture_body = previous_capture
        incomplete = (not finished or stats.status != "ok" or stats.capped or stats.malformed
                      or batch.received != len(batch.observations)
                      or (batch.declared_total is not None and batch.received < batch.declared_total))
        if stats.status == "not_configured":
            status = "skipped:missing_credentials"
        elif incomplete:
            status = "partial" if stats.pages else "failed"
        else:
            status = "ok"
        batch.commit(status)


def record_usajobs_skip(reason, max_pages):
    with configured_run(dict(collector="usajobs", max_pages=max_pages)) as run:
        if run is not None:
            _ObservationBatch(*run, USAJOBS_CHANNEL).commit(f"skipped:{reason}")


def collect_greenhouse(store, run_id, transport, board, max_pages=3, detail_budget=3, market=None):
    """Return the existing board output and write through the full observed inventory."""
    from career_engine.ats import retrieve_board

    channel = dict(kind="ats", platform="greenhouse", locator=board["url"], channel_key=board["key"])
    batch = _ObservationBatch(store, run_id, channel)
    start = len(transport.requests)
    stats = None
    try:
        rows, stats = retrieve_board(transport, board, max_pages, detail_budget, market, _evidence=batch)
        return rows, stats
    finally:
        errors = stats["errors"] if stats is not None else []
        inventory = stats["inventory"] if stats is not None else {}
        batch.declared_total = _total(inventory.get("provider_claimed_total"))
        if len(transport.requests) == start and errors and errors[0] in SKIP_REASONS:
            status = "skipped:" + errors[0]
        elif not errors and inventory.get("metadata_complete"):
            status = "ok"
        else:
            status = "partial" if batch.observations or inventory.get("observed_available_items") is not None else "failed"
        batch.commit(status)
