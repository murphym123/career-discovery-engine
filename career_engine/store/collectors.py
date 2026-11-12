"""Opt-in persistence at the existing USAJOBS, Greenhouse, and Lever boundaries."""
import hashlib
import json
import os
import re
from contextlib import contextmanager
from urllib.parse import urlsplit

from career_engine.store import Store, git_sha, timestamp
from career_engine.store.privacy import REDACTED, Projection, contains_configured_secret

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
    def __init__(self, store, run_id, channel, *, private_values=(), require_matching_role=False):
        self.store = store
        self.run_id = run_id
        self.channel_id = store.ensure_channel(**channel, retention_class="public",
                                              evidence_role="discovery", verification_state="verified",
                                              require_matching_role=require_matching_role)
        self.started_at = store.now()
        self.receipts = []
        self.observations = []
        self.details = {}
        self.inventory = []
        self.unrepresented = 0
        self.declared_total = None
        self.received = 0
        self.privacy = Projection()
        self.private_values = tuple(value for value in private_values if value)

    def _echoes_request_credential(self, body):
        if not self.private_values:
            return False
        text = body.decode("utf-8", errors="replace")
        if contains_configured_secret(text, self.private_values):
            return True
        # JSON escapes can encode a credential without its literal bytes.
        if b"\\" not in body and b"\x00" not in body:
            return False
        try:
            pending = [json.loads(body)]
        except (ValueError, RecursionError):
            return False
        while pending:
            value = pending.pop()
            if isinstance(value, str) and contains_configured_secret(value, self.private_values):
                return True
            if isinstance(value, dict):
                pending.extend(value)
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
        return False

    def capture(self, body, role):
        page = None
        if role == "inventory":
            page = dict(disposition="missing", raw_sha=None, sightings=0)
            self.inventory.append(page)
        if not isinstance(body, bytes):
            return False
        if self._echoes_request_credential(body):
            if page is not None:
                page["disposition"] = "secret_echo"
            return True  # Tell the opt-in regional report to withhold this body as well.
        sha = self.store.blobs.put(body, "public")
        self.receipts.append((sha, role, len(body), self.store.now()))
        if page is not None:
            page.update(disposition="retained", raw_sha=sha)
        return False

    def usajobs_response(self, body):
        return self.capture(body, "inventory")

    def _request_secret(self, value):
        return value is not None and contains_configured_secret(str(value), self.private_values)

    def usajobs_report_row(self, row):
        # Keep the legacy row shape while excluding known request credentials.
        return {
            key: ("" if key in ("source_id", "url") else REDACTED)
            if self._request_secret(value) else value
            for key, value in row.items()
        }

    def usajobs_page(self, total, items):
        from career_engine.sources import normalize

        self.declared_total = None if self._request_secret(total) else _total(total)
        self.received += len(items)
        # Account for the fetched page even if a legacy caller stops iterating.
        for item in items:
            try:
                row = normalize("usajobs", item)
            except (ValueError, TypeError, AttributeError):
                if not isinstance(item, dict):
                    self.unrepresented += 1
                    continue
                fields = item.get("MatchedObjectDescriptor")
                fields = fields if isinstance(fields, dict) else {}
                row = dict(
                    title=fields.get("PositionTitle"), url=fields.get("PositionURI"),
                    company=fields.get("OrganizationName"),
                    location=fields.get("PositionLocationDisplay"),
                )
            source_id = item.get("MatchedObjectId")
            native_id = str(source_id) if type(source_id) in (str, int) else None
            self.usajobs_sighting(row, native_id)

    def observe(self, row, identity_key, native_id):
        if not self.inventory:
            self.capture(None, "inventory")
        page = self.inventory[-1]
        page["sightings"] += 1
        self.observations.append((row.copy(), identity_key, native_id, page["raw_sha"], self.store.now()))

    def usajobs_sighting(self, row, native_id):
        if (native_id == "None" or self.privacy.native(native_id) is None
                or self._request_secret(native_id)):
            native_id = None
        url = str(row["url"] or "")
        if native_id is None:
            canonical = self.privacy.url(url)
            if not canonical:
                self.unrepresented += 1
                return
            identity = "url:" + canonical
        else:
            identity = "usajobs:usajobs:" + native_id
        self.observe(row, identity, native_id)

    def greenhouse_inventory(self, board, items, company):
        from career_engine.ats import normalize_posting, posting_key

        self.received = len(items)
        if not self.inventory:
            self.capture(None, "inventory")
        for item in items:
            try:
                row = normalize_posting(board, item, company)
            except (ValueError, TypeError, AttributeError):
                # A provider ID still identifies the posting when its link is
                # missing or fails the legacy URL checks.
                native_id = str(item.get("id", "")) if isinstance(item, dict) else ""
                public_url = ""
                if not native_id.isdigit() and isinstance(item, dict):
                    public_url = self.privacy.url(str(item.get("absolute_url") or ""))
                    url_key = posting_key(public_url) if public_url else ""
                    if url_key.startswith(board["key"] + ":"):
                        native_id = url_key.rsplit(":", 1)[-1]
                if not native_id.isdigit():
                    self.unrepresented += 1
                    continue
                self.observe(
                    dict(title=item.get("title"), company=item.get("company_name") or company,
                         location=item.get("location", {}).get("name") if isinstance(item.get("location"), dict)
                         else item.get("location"), url=public_url, posted_at=item.get("first_published"),
                         closing_at=item.get("application_deadline")),
                    f"{board['key']}:{native_id}", native_id,
                )
                continue
            self.observe(row, row["identity"], row["identity"].rsplit(":", 1)[-1])

    def _safe_text(self, value):
        return REDACTED if self._request_secret(value) else self.privacy.text(str(value or ""))

    def greenhouse_detail(self, row):
        self.details[row["identity"]] = row

    def commit(self, status, *, employer_name=None):
        raw_sha = next((sha for sha, role, _, _ in self.receipts if role == "inventory"), None)
        if self.unrepresented and status == "ok":
            status = "partial"
        with self.store.transaction():
            for sha, _, byte_count, captured_at in self.receipts:
                self.store.register_capture(sha, byte_count, captured_at=captured_at)
            poll_id = self.store.record_poll(
                self.run_id, self.channel_id, started_at=self.started_at, status=status,
                received=self.received, declared_total=self.declared_total, raw_sha=raw_sha,
                captures=((sha, role) for sha, role, _, _ in self.receipts),
                unrepresented=self.unrepresented,
                inventory=((page["disposition"], page["raw_sha"], page["sightings"]) for page in self.inventory),
            )
            for row, identity_key, native_id, capture_sha, observed_at in self.observations:
                detail = self.details.pop(identity_key, None)
                description_row = detail if detail and detail.get("description") else row
                description = description_row.get("description")
                description_sha = None
                if description and not self._request_secret(description):
                    description_sha = description_row.get("description_sha256") or hashlib.sha256(description.encode()).hexdigest()
                canonical_url = str(row["url"] or "")
                observed_url = str(row.get("original_posting_url") or canonical_url)
                self.store.record_sighting(
                    poll_id, identity_key=identity_key, native_id=native_id,
                    url="" if self._request_secret(observed_url) else observed_url,
                    canonical_url="" if self._request_secret(canonical_url) else canonical_url,
                    title=self._safe_text(row.get("title")),
                    employer_name_raw=self._safe_text(employer_name if employer_name is not None else row.get("company")),
                    location_raw=self._safe_text(row.get("location")),
                    posted_at=None if self._request_secret(row.get("posted_at")) else _source_date(row.get("posted_at")),
                    closing_at=None if self._request_secret(row.get("closing_at")) else _source_date(row.get("closing_at")),
                    observed_at=observed_at, description_sha=description_sha, raw_sha=capture_sha,
                )


class _LeverObservationBatch(_ObservationBatch):
    def __init__(self, store, run_id, board):
        privacy = Projection()
        locator = privacy.url(board["url"])
        if locator != board["url"]:
            raise ValueError("Expected a shareable public Lever board")
        channel = dict(kind="ats", platform=board["provider"], locator=locator, channel_key=board["key"])
        super().__init__(store, run_id, channel, private_values=privacy.secrets, require_matching_role=True)
        self.board = board
        self.pages_observed = 0
        self.board_display_name = ""
        self.withheld_capture = False

    def capture(self, body, role):
        withheld = super().capture(body, role)
        self.withheld_capture |= withheld
        return withheld

    def report_rows(self, rows):
        if not self.withheld_capture:
            return rows

        def redact(value):
            if isinstance(value, dict):
                return {key: redact(field) for key, field in value.items()}
            if isinstance(value, list):
                return [redact(field) for field in value]
            return REDACTED if isinstance(value, str) and self._request_secret(value) else value

        return redact(rows)

    def lever_page(self, count):
        if len(self.inventory) == self.pages_observed:
            self.capture(None, "inventory")
        self.pages_observed += 1
        self.received += count

    def lever_sighting(self, item, row):
        from career_engine.ats import LEVER_REMOTE_LOCATION, _location, canonical_posting_url, posting_key
        from career_engine.extract import safe_public_url

        if not isinstance(item, dict):
            self.unrepresented += 1
            return

        prefix = self.board["key"] + ":"
        native_id = item.get("id")
        if (not isinstance(native_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,100}", native_id)
                or native_id.lower() in ("none", "null") or not self.privacy.native(native_id)):
            native_id = None
        else:
            native_id = self.privacy.native(native_id.lower())

        raw_url = item.get("hostedUrl")
        raw_url = raw_url if isinstance(raw_url, str) and safe_public_url(raw_url) else ""
        url_key = posting_key(raw_url) if raw_url else ""
        parsed_url = urlsplit(raw_url)
        parts = parsed_url.path.strip("/").split("/")
        lever_route = (parsed_url.hostname in ("jobs.lever.co", "jobs.eu.lever.co")
                       and (len(parts) == 2 or len(parts) == 3 and parts[2] == "apply"))
        canonical = self.privacy.url(canonical_posting_url(raw_url)) if raw_url else None
        if native_id is None and lever_route and url_key.startswith(prefix) and canonical:
            native_id = self.privacy.native(url_key.removeprefix(prefix))
        if native_id is None:
            self.unrepresented += 1
            return
        identity = prefix + native_id

        observed = self.privacy.url(raw_url)
        if url_key and url_key != identity:
            # A link to a different opening cannot identify or match this sighting.
            observed = canonical = None
        if not observed and lever_route and url_key == identity:
            # An independently recognizable public Lever route is usable without
            # the sensitive query on the originally supplied URL.
            observed = canonical
        if not observed:
            canonical = None

        categories = item.get("categories")
        categories = categories if isinstance(categories, dict) else {}
        additional = categories.get("allLocations")
        if row is None:
            row = {"title": item.get("text") if isinstance(item.get("text"), str) else ""}
            additional = [additional] if isinstance(additional, str) else additional
            additional = additional if isinstance(additional, list) else []
            locations = [_location(value) for value in [categories.get("location"), *additional]]
        elif isinstance(additional, str):
            locations = [_location(categories.get("location")), additional]
        else:
            locations = row["raw_locations"]
        if str(item.get("workplaceType")).lower() == "remote":
            locations = [LEVER_REMOTE_LOCATION, *locations]
        location = " ; ".join(dict.fromkeys(self._safe_text(place) for place in locations if place))
        if self.inventory[-1]["disposition"] == "secret_echo":
            if "description_sha256" in row:
                row["description_sha256"] = ""
            row = dict(row, description="")
        self.observe(dict(row, location=location, url=canonical or observed or "",
                          original_posting_url=observed or ""), identity, native_id)

    def _safe_text(self, value):
        value = super()._safe_text(value)
        return REDACTED if any(0xD800 <= ord(char) <= 0xDFFF for char in value) else value

    def commit(self, status):
        if not self.pages_observed:
            return super().commit(status)
        display = self._safe_text(self.board_display_name)
        if not display or display == REDACTED:
            display = self._safe_text(self.board["token"])
        with self.store.transaction():
            self.store.ensure_channel_employer(
                self.channel_id, display, "lever_board",
                aliases=({"kind": "lever_tenant", "value": self._safe_text(self.board["key"])},),
            )
            super().commit(status, employer_name=display)


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
        for row in retrieve(session, "usajobs", max_pages, stats, _evidence=batch):
            yield batch.usajobs_report_row(row)
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


def collect_lever(store, run_id, transport, board, max_pages=3, detail_budget=3, market=None):
    """Keep Lever's existing return values and persist every fetched inventory page."""
    from career_engine.ats import LEVER_PROVIDERS, board_from_url, retrieve_board

    parsed = board_from_url(board.get("url", ""))
    if (not parsed or parsed["provider"] not in LEVER_PROVIDERS
            or any(board.get(field) != value for field, value in parsed.items())):
        raise ValueError("Invalid observed Lever board")
    batch = _LeverObservationBatch(store, run_id, board)
    start = len(transport.requests)
    stats = None
    try:
        rows, stats = retrieve_board(transport, board, max_pages, detail_budget, market, _evidence=batch)
        return batch.report_rows(rows), stats
    finally:
        errors = stats["errors"] if stats is not None else []
        if len(transport.requests) == start and errors and errors[0] in SKIP_REASONS:
            status = "skipped:" + errors[0]
        elif stats is not None and not errors and stats["inventory"]["metadata_complete"]:
            status = "ok"
        else:
            status = "partial" if batch.pages_observed else "failed"
        batch.commit(status)
