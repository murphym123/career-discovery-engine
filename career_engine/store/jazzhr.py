"""Durable intake for the existing JazzHR public index and per-board detail pass."""
import re
from urllib.parse import unquote, urljoin, urlsplit

from career_engine.ats import FetchError, PublicTransport, board_from_url, posting_key
from career_engine.dc_sources import Document
from career_engine.jazzhr import DETAIL_CAPTURE_PREFIX, retrieve_jazzhr
from career_engine.public_sources import public_text
from career_engine.store.collectors import SKIP_REASONS, _ObservationBatch
from career_engine.store.privacy import AUTH_PATH, REDACTED, SENSITIVE_TEXT, Projection


ACCESS_TITLE = re.compile(
    r"\s*(?:just a moment\b|please.*(?:robot|verify)|access denied\b|forbidden\b|"
    r"security check\b|verify (?:you|your browser)\b|sign[ -]?in\b|log[ -]?in\b)", re.I
)
PRIVATE_STATE_NAME = re.compile(
    r"(?:(?:csrf|xsrf)(?:[_-]?token)?|(?:access|auth|session|candidate|applicant)[_-]?(?:token|key|id)|"
    r"password|authenticity_token|__requestverificationtoken)", re.I
)
GENERIC_CSRF = re.compile(r"_?(?:(?:csrf|xsrf)(?:[_-]?token)?|authenticity_token|__requestverificationtoken)", re.I)
CSRF_NONCE = re.compile(r"[A-Za-z0-9._-]{1,128}")
PRIVATE_STATE_SCRIPT = re.compile(
    rf"""\b{PRIVATE_STATE_NAME.pattern}["']?\s*[:=]\s*["'][^"']+["']""", re.I
)
PRIVATE_PUBLIC_PAGE_STATE = re.compile(
    r"token|credentials?|api[_-]?key|authorization|secret|refresh[_-]?token|"
    r"session[_-]?id|(?:candidate|applicant)[_-]?(?:data|profile|resume|email|phone|contact|name)|"
    r"candidates?|applicants?|private[_-]?key|signed[_-]?url", re.I
)
PRIVATE_DETAIL_STATE = re.compile(
    r"email|phone|telephone|first[_-]?name|last[_-]?name|full[_-]?name|ssn|resume|"
    r"cover[_-]?letter|cookie|signature|session", re.I
)
STATE_ASSIGNMENT = re.compile(r"""(?<![\w-])([\w-]+)["']?\s*(?:\]\s*)?(?::|=(?![=>]))\s*""")
JAVASCRIPT_TYPES = {"", "module", "text/javascript", "application/javascript"}
JS_IDENTIFIER = r"[$A-Za-z_][\w$]*"
JS_VALUE_END = r"(?=\s*(?:[,;})\]]|$))"
# Recognize field references and simple calls without credential literals.
# Quoted selectors are allowed only as arguments to familiar DOM lookups.
PASSWORD_EXPRESSION = re.compile(
    rf"""(?:!\s*)?(?:(?:\$|document\.(?:getElementById|querySelector))\(\s*(["'])"""
    rf"""(?:#?password|input\[name=password\])\1\s*\)|{JS_IDENTIFIER})"""
    rf"""(?:\s*\.\s*{JS_IDENTIFIER}|\s*\(\s*(?:{JS_IDENTIFIER})?\s*\))*{JS_VALUE_END}"""
)
SESSION_LITERAL = re.compile(rf"""(["'])([A-Za-z0-9._-]{{1,128}})\1{JS_VALUE_END}""")
EMPTY_STATE = re.compile(rf"""(?:""|''|null|false){JS_VALUE_END}""")
PRIVATE_PUBLIC_PAGE_TITLE = re.compile(
    r"\s*(?:(?:candidate|applicant)\s+(?:portal|profile|dashboard|account)|"
    r"(?:account|employee)\s+(?:login|sign[ -]?in)|my applications)\b", re.I
)
# A prefilled public search/filter form is not applicant data. Keep detecting
# populated personal fields and private state, regardless of form purpose.
PERSONAL_FORM_FIELD = re.compile(
    r"email|phone|telephone|(?:first|last|full|middle)[_-]?name|address|"
    r"date[_-]?of[_-]?birth|birthdate|dob|social[_-]?security|ssn|"
    r"resume|cover[_-]?letter|candidate|applicant", re.I
)


def _capture_rejection(body, *, public_inventory=False, detail_url=None):
    public_detail = detail_url is not None
    public_jazzhr = public_inventory or public_detail

    def private_name(name, *, csrf_metadata=False, value=""):
        if (public_jazzhr and csrf_metadata and GENERIC_CSRF.fullmatch(name)
                and (not public_detail or CSRF_NONCE.fullmatch(value) and not SENSITIVE_TEXT.search(value))):
            return False
        return (PRIVATE_STATE_NAME.search(name)
                or (public_jazzhr and PRIVATE_PUBLIC_PAGE_STATE.fullmatch(name))
                or (public_detail and PRIVATE_DETAIL_STATE.fullmatch(name)))

    def private_script(script, *, executable=False):
        for match in STATE_ASSIGNMENT.finditer(script):
            name, value_start = match[1], match.end()
            if public_detail and GENERIC_CSRF.fullmatch(name):
                # Detail pages may advertise a scalar form nonce. Keep
                # unrecognized expressions, containers and escaped JS withheld.
                nonce = SESSION_LITERAL.match(script, value_start)
                if nonce and not SENSITIVE_TEXT.search(nonce[2]):
                    continue
                return True
            if not private_name(name, csrf_metadata=script[value_start:value_start + 1] not in ("{", "[")):
                continue
            if executable:
                if name.lower() == "password":
                    # Keep the existing inventory exception for negation.
                    # Detail pages only allow recognized field expressions.
                    rhs = script[value_start:].lstrip()
                    if PASSWORD_EXPRESSION.match(script, value_start) or (
                        public_inventory and rhs.startswith("!") and rhs.lstrip("!").lstrip()
                        and not rhs.lstrip("!").lstrip().startswith(("\'", '"', chr(96)))
                    ):
                        continue
                if public_inventory and name.lower() == "sessionid":
                    # Anonymous inventory can carry a public page session ID.
                    # Keep the exception to scalar state in executable scripts.
                    session = SESSION_LITERAL.match(script, value_start)
                    if session and not SENSITIVE_TEXT.search(session[2]):
                        continue
            if public_detail and not PRIVATE_STATE_NAME.search(name):
                # Empty JSON fields and null/false state do not carry applicant
                # data. Never use this exception for credential-like names.
                if EMPTY_STATE.match(script, value_start):
                    continue
            return True
        return False

    doc = Document(body.decode("utf-8", errors="replace")).root
    privacy = Projection() if detail_url else None
    rejection = None
    for node in doc.walk():
        attrs = node.attrs
        name = attrs.get("name") or ""
        if ((node.tag in ("title", "h1") and ACCESS_TITLE.match(node.text()))
                or (public_jazzhr and node.tag in ("title", "h1", "h2")
                    and PRIVATE_PUBLIC_PAGE_TITLE.match(node.text()))
                or (attrs.get("id") or "").lower() in ("challenge-form", "cf-challenge-running")):
            return "access_challenge_not_bypassed"
        if public_jazzhr and node.tag == "form" and attrs.get("action"):
            action = unquote(urlsplit(attrs["action"]).path)
            if any(AUTH_PATH.fullmatch(part) for part in action.split("/")):
                return "access_challenge_not_bypassed"
        if node.tag == "meta" and attrs.get("content") and private_name(
                name, csrf_metadata=True, value=attrs["content"]):
            rejection = "private_page_state"
        if privacy and any(("?" in target or target.startswith("//"))
                           and privacy.url(urljoin(detail_url, target)) is None
                           for target in (attrs.get(attr) or "" for attr in ("href", "action", "src", "formaction"))):
            rejection = "private_page_state"
        if public_jazzhr and any(key.startswith("data-") and value and private_name(
                key[5:], csrf_metadata=True, value=value)
                for key, value in attrs.items()):
            rejection = "private_page_state"
        if public_jazzhr and any(key.startswith(("data-", "on")) and value and private_script(value)
                                 for key, value in attrs.items()):
            rejection = "private_page_state"
        if node.tag == "script":
            script = "".join(child for child in node.children if isinstance(child, str))
            media = (attrs.get("type") or "").lower().split(";", 1)[0].strip()
            executable = media in JAVASCRIPT_TYPES and not script.lstrip().startswith(("{", "["))
            if (private_script(script, executable=executable)
                    if public_jazzhr else PRIVATE_STATE_SCRIPT.search(script)):
                rejection = "private_page_state"
        if node.tag in ("input", "textarea"):
            kind = (attrs.get("type") or "").lower()
            if kind == "password":
                return "access_challenge_not_bypassed" if public_jazzhr else "private_page_state"
            if public_inventory and kind == "file" and PERSONAL_FORM_FIELD.search(name):
                rejection = "private_page_state"
            value = attrs.get("value") if node.tag == "input" else node.text()
            if (public_detail and kind in ("radio", "checkbox") and "checked" not in attrs
                    and not private_name(name)):
                value = None
            if value and (private_name(name, csrf_metadata=kind == "hidden", value=value)
                          or ((public_jazzhr or node.closest("form")) and
                              (kind in ("email", "tel") or
                               PERSONAL_FORM_FIELD.search(name) or
                               node.tag == "textarea" and kind != "search"))):
                rejection = "private_page_state"
            if (public_detail and value and kind in ("", "text", "date", "number", "url", "file")
                    and node.closest("form")):
                rejection = "private_page_state"
        if public_detail and node.tag == "option" and "selected" in attrs:
            select = node.closest("select")
            if select and select.closest("form") and attrs.get("value", node.text()):
                rejection = "private_page_state"
        if rejection and not public_jazzhr:
            return rejection
    return rejection


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
        self.first_occurrences = {}
        self.detail_routes = set()

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
                   if node.tag == "script" and (node.attrs.get("type") or "").lower().split(";", 1)[0].strip()
                   in ("application/ld+json", "application/json"))

    def read_page(self, transport, url, *, detail_identity=None):
        def shareable_route(target):
            return (self.privacy.url(target) == target and not self._request_secret(target)
                    and (detail_identity is None or (
                        target.rstrip("/") in self.detail_routes
                        and posting_key(target) == detail_identity)))

        if not shareable_route(url):
            raise FetchError("unsafe_public_job_url")
        body = None
        rejection = None

        def inspect(response):
            nonlocal body, rejection
            try:
                final_url = transport.requests[-1]["url"]
                is_detail = detail_identity is not None and shareable_route(final_url)
                if is_detail and self._echoes_request_credential(response):
                    self.withheld = True
                    rejection = "private_page_state"
                    # A confirmed credential echo is a global stop in the pilot.
                    return True
                rejection = _capture_rejection(
                    response, public_inventory=detail_identity is None and final_url in (url, url + "/"),
                    detail_url=final_url if is_detail else None,
                )
            except (TypeError, ValueError, RecursionError):
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
            if posting_key(observed) == row["identity"]:
                self.detail_routes.add(canonical.rstrip("/"))
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
            raise FetchError("private_page_state")
        identity = row["identity"]
        native_id = identity.rsplit(":", 1)[-1]
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
        elif (stats is None or stats["status"] == "partial_failure"
              or self.index_incomplete or self.unrepresented):
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
