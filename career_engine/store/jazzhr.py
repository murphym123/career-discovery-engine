"""Durable intake for the existing JazzHR public index and per-board detail pass."""
import re
from urllib.parse import urlsplit

from career_engine.ats import FetchError, PublicTransport, board_from_url
from career_engine.dc_sources import Document
from career_engine.jazzhr import DETAIL_CAPTURE_PREFIX, retrieve_jazzhr
from career_engine.public_sources import public_text
from career_engine.store.collectors import SKIP_REASONS, _ObservationBatch
from career_engine.store.privacy import REDACTED, Projection


ACCESS_TITLE = re.compile(
    r"\s*(?:just a moment\b|please.*(?:robot|verify)|access denied\b|forbidden\b|"
    r"security check\b|verify (?:you|your browser)\b|sign[ -]?in\b|log[ -]?in\b)", re.I
)
PRIVATE_STATE_NAME = re.compile(
    r"(?:(?:csrf|xsrf)(?:[_-]?token)?|(?:access|auth|session|candidate|applicant)[_-]?(?:token|key|id)|"
    r"password|authenticity_token|__requestverificationtoken)", re.I
)
PRIVATE_STATE_SCRIPT = re.compile(
    rf"""\b{PRIVATE_STATE_NAME.pattern}["']?\s*[:=]\s*["'][^"']+["']""", re.I
)


def _capture_rejection(body):
    doc = Document(body.decode("utf-8", errors="replace")).root
    for node in doc.walk():
        attrs = node.attrs
        if ((node.tag in ("title", "h1") and ACCESS_TITLE.match(node.text()))
                or (attrs.get("id") or "").lower() in ("challenge-form", "cf-challenge-running")):
            return "access_challenge_not_bypassed"
        if node.tag == "meta" and attrs.get("content") and PRIVATE_STATE_NAME.search(attrs.get("name") or ""):
            return "private_page_state"
        if node.tag == "script" and any(PRIVATE_STATE_SCRIPT.search(text) for text in node.children
                                        if isinstance(text, str)):
            return "private_page_state"
        if node.tag in ("input", "textarea"):
            kind = (attrs.get("type") or "").lower()
            if kind == "password":
                return "private_page_state"
            value = attrs.get("value") if node.tag == "input" else node.text()
            if value and (PRIVATE_STATE_NAME.search(attrs.get("name") or "")
                          or (node.closest("form") and kind not in ("submit", "button", "reset", "radio", "checkbox"))):
                return "private_page_state"
    return None


class _JazzHRObservationBatch(_ObservationBatch):
    def __init__(self, store, run_id, board):
        privacy = Projection()
        if privacy.url(board["url"]) != board["url"]:
            raise ValueError("Expected a shareable public JazzHR board")
        channel = dict(kind="ats", platform="jazzhr", locator=board["url"], channel_key=board["key"])
        super().__init__(store, run_id, channel, private_values=privacy.secrets, require_matching_role=True)
        self.board = board
        self.board_name = ""
        self.empty = False
        self.index_incomplete = False
        self.withheld = False
        self.detail_incomplete = False
        self.first_occurrences = {}

    def _echoes_request_credential(self, body):
        if super()._echoes_request_credential(body):
            return True
        if not self.private_values or b"\\" not in body:
            return False
        # HTML can wrap JSON-LD containing JSON-escaped credential characters.
        doc = Document(body.decode("utf-8", errors="replace")).root
        check_json = super()._echoes_request_credential
        return any(check_json("".join(text for text in node.children if isinstance(text, str)).encode())
                   for node in doc.walk()
                   if node.tag == "script" and node.attrs.get("type") == "application/ld+json")

    def read_page(self, transport, url):
        def shareable_route(target):
            return self.privacy.url(target) is not None and not self._request_secret(target)

        if not shareable_route(url):
            raise FetchError("unsafe_public_job_url")
        body = None
        rejection = None

        def inspect(response):
            nonlocal body, rejection
            try:
                rejection = _capture_rejection(response)
            except (ValueError, RecursionError):
                rejection = "unreadable_public_html"
            if rejection:
                return rejection
            body = response
            withheld = self._echoes_request_credential(response)
            self.withheld |= withheld
            return withheld

        html = public_text(transport, url, max_bytes=1_000_000, kind="public_html", capture=inspect,
                           redirect_filter=shareable_route)
        if rejection:
            raise FetchError(rejection)
        return html, body

    def capture_index(self, body, final_url):
        if urlsplit(final_url).path.rstrip("/") != "/apply":
            raise FetchError("board_index_redirected")
        self.withheld |= self.capture(body, "inventory")

    def index_card(self, row, observed_url):
        self.received += 1
        native_id = row["identity"].rsplit(":", 1)[-1] if row else None
        if self.privacy.native(native_id) is None:
            self.unrepresented += 1
            return
        canonical = self.privacy.url(row["url"])
        if canonical and not PublicTransport.allowed(canonical):
            canonical = None
        observed = self.privacy.url(observed_url) if canonical else None
        if observed:
            observed = urlsplit(observed)._replace(query="").geturl()
        self.index_incomplete |= not canonical or not row["title"]
        projected = dict(row, url=canonical or "", original_posting_url=observed or canonical or "")
        self.observe(projected, row["identity"], native_id)
        self.first_occurrences.setdefault(row["identity"], self.observations[-1][0])

    def index_finished(self, name, empty, continuation):
        self.board_name = name
        self.index_incomplete |= continuation
        self.empty = empty and not self.received and not continuation

    def record_detail(self, body, row):
        if body is None or self._echoes_request_credential(body):
            self.withheld |= body is not None
            self.detail_incomplete = True
            row["description_sha256"] = ""
            return
        identity = row["identity"]
        native_id = identity.rsplit(":", 1)[-1]
        if identity not in self.first_occurrences:
            self.detail_incomplete = True
            return
        self.capture(body, DETAIL_CAPTURE_PREFIX + native_id)
        self.details[identity] = row
        for field in ("posted_at", "closing_at"):
            self.first_occurrences[identity][field] = row[field]

    def finish(self, stats, stopped):
        errors = stats["errors"] if stats is not None else []
        if stopped in SKIP_REASONS and errors:
            status = f"skipped:{stopped}"
        elif self.empty and errors == ["no_public_index_rows_or_schema_changed"]:
            status = "ok"
        elif not self.observations:
            status = "failed"
        elif stats is None or errors or self.index_incomplete or self.unrepresented or self.detail_incomplete:
            status = "partial"
        else:
            status = "ok"
        with self.store.transaction():
            if self.observations or self.empty:
                name = self._safe_text(self.board_name)
                if not name or name == REDACTED:
                    name = self._safe_text(self.board["token"])
                self.store.ensure_channel_employer(
                    self.channel_id, name, "jazzhr_board",
                    aliases=({"kind": "jazzhr_tenant", "value": self.board["key"]},),
                )
            self.commit(status)


def collect_jazzhr(store, run_id, transport, board, detail_budget=1, market=None):
    """Return the legacy brief rows and persist each observed index card."""
    parsed = board_from_url(board.get("url", ""))
    if (not parsed or parsed["provider"] != "jazzhr"
            or any(board.get(field) != value for field, value in parsed.items())):
        raise ValueError("Invalid observed JazzHR board")
    batch = _JazzHRObservationBatch(store, run_id, board)
    start = len(transport.requests)
    stats = None
    try:
        rows, stats = retrieve_jazzhr(transport, board, detail_budget, market, _evidence=batch)
        return (batch._redact_credential_echo(rows) if batch.withheld else rows), stats
    finally:
        stopped = ""
        if len(transport.requests) == start:
            stopped = transport.global_stop_reason()
            if not stopped and urlsplit(board["url"]).hostname in transport.blocked:
                stopped = "host_stopped_after_denial"
        batch.finish(stats, stopped)
