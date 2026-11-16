"""Issue #39 controlled pilot and offline precheck."""
from collections import Counter
from contextlib import contextmanager
from datetime import timezone
from email.utils import parsedate_to_datetime
import html
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit

from career_engine.ats import FetchError, PublicTransport, board_from_url, posting_key, stamp
from career_engine.dc_sources import Document
from career_engine.extract import USER_AGENT
from career_engine.jazzhr import jazz_index
from career_engine.markets import DC_MARKET
from career_engine.store import timestamp
from career_engine.store.collectors import collect_greenhouse, collect_lever
from career_engine.store.jazzhr import GENERIC_CSRF, _capture_rejection, collect_jazzhr
from career_engine.store.privacy import AUTH_PATH, Projection, SECRET_NAME, contains_configured_secret
from tools.pilot39_http import HTTPSWire, Reply
from tools.pilot39_robots import RobotsRules

COHORT_SHA = "443ac7c071d35eed2349ae1a7d3f70b1b1a6dcd917e2ca296c900eee3a9ff7de"
DETAIL_SLOTS = {2, 5, 8, 11, 14, 23}
STAGE_LIMITS = {"terms": 16, "discovery": 10, "seeded": 96, "cold": 26, "repeat": 12}
TOTAL_LIMIT = 160
BYTE_LIMIT = 128 * 1024 * 1024
COLLECTORS = {"greenhouse": collect_greenhouse, "lever": collect_lever, "jazzhr": collect_jazzhr}
INDEX_HOST = "index.commoncrawl.org"
FEED_HOST = "app.jazz.co"
DENIAL = {401, 403, 429}
CHALLENGE = "access_challenge_not_bypassed"
PRIVATE = "private_page_state"  # Confirmed secret/unsafe response: global stop.
SOURCE_PRIVATE = "private_page_withheld"  # Suspect public HTML: reject only this source.
REDIRECT = "unsafe_redirect"
POLICY = "terms_unverified"
TERMS_DENIED = "terms_disallowed"
PRIVATE_FIELD = re.compile(
    r"api[_-]?key|(?:access|refresh|auth|session|csrf|xsrf)?[_-]?token|authorization|"
    r"password|secret|session[_-]?id|(?:candidate|applicant)[_-]?(?:id|email|phone|data|profile)|"
    r"candidates|applicants|cookie|credentials?|private[_-]?key|signature|signed[_-]?url", re.I,
)
JAZZHR_JSON_PERSONAL_FIELD = re.compile(
    r"(?:candidate|applicant)[_-]?(?:resume|contact|name)|resume|cover[_-]?letter|"
    r"email|phone|first[_-]?name|last[_-]?name|full[_-]?name|ssn", re.I,
)
JAZZHR_DETAIL_PRIVATE_FIELD = re.compile(r"session|applicant|candidate|telephone|addressLine[12]|streetAddress", re.I)
SECRET_ASSIGNMENT = re.compile(
    r"\bBearer\s+[A-Za-z0-9._~+/=-]+|\b(?:gh[pousr]_[A-Za-z0-9]+|github_pat_[A-Za-z0-9_]+|"
    r"AKIA[A-Z0-9]{16}|sk-[A-Za-z0-9_-]{12,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)",
    re.I,
)
URL_IN_BODY = re.compile(r"""https?://[^\s<>"'\\]+""", re.I)
TOKEN_ASSIGNMENT = re.compile(r"""\b(?:token|password|sessionid|credential)\s*["']?\s*[:=]""", re.I)


def unsafe_body(text):
    decoded = html.unescape(text)
    if SECRET_ASSIGNMENT.search(decoded):
        return True
    for match in URL_IN_BODY.finditer(decoded):
        value = match.group()
        if "?" not in value and "@" not in value:
            continue
        try:
            parsed = urlsplit(value)
            query = parse_qsl(parsed.query, max_num_fields=128)
        except ValueError:
            return True
        if parsed.username or parsed.password or any(SECRET_NAME.fullmatch(key) for key, _ in query):
            return True
    return False


def sha(data):
    return hashlib.sha256(data).hexdigest()


def check_cohort(urls):
    boards = [board_from_url(url) for url in urls]
    if (len(urls) != 24 or len(set(urls)) != 24 or sha(("\n".join(urls) + "\n").encode()) != COHORT_SHA
            or any(not board or board["url"] != url or board["provider"] not in COLLECTORS
                   or Projection().url(url) != url for url, board in zip(urls, boards))):
        raise ValueError("Cohort does not match the frozen public board list")
    return boards


# Explicitly public marketing attribution, never access tokens or arbitrary
# query parameters. Query values are not retained, even in the candidate ledger.
PUBLIC_DISCOVERY_QUERY = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "source", "src", "ref", "referrer",
})
COLD_SCREENING_KEYS = (
    "registered_boards", "board_url_rejected", "no_eligible_route",
    "evidence_source_rejected", "observation_rejected",
    "observation_query_rejected", "observation_route_rejected",
    "evidence_accepted", "qualified_boards", "seed_overlaps",
    "cold_candidates", "selected_boards",
)


def _public_observation(value, board_url, projection):
    """Return a sanitized public board/posting URL or a non-identifying reason.

    Publisher sitemaps often append public referral tags. Requiring literal URL
    equality rejected these otherwise valid hints. Only approved attribution
    keys may be stripped; signed, unknown, or private query keys remain denied.
    """
    projected = projection.url(value)
    if not projected:
        return None, "observation_rejected"
    original, safe = urlsplit(value), urlsplit(projected)
    if original.fragment or original.scheme != "https" or original.hostname != safe.hostname or original.path != safe.path:
        return None, "observation_rejected"
    if projected != value:
        try:
            params = parse_qsl(original.query, keep_blank_values=True, max_num_fields=128)
        except ValueError:
            return None, "observation_query_rejected"
        if (not params or any(
            key not in PUBLIC_DISCOVERY_QUERY or len(val) > 80 or projection.unsafe(val)
            for key, val in params
        )):
            return None, "observation_query_rejected"
    board = board_from_url(board_url)
    if (board_from_url(projected) != board
            or projected != board_url and not posting_key(projected)
            or board["provider"] == "jazzhr" and not PublicTransport.allowed(projected)):
        return None, "observation_route_rejected"
    return projected, None


def select_cold(registry, seeded, screening=None):
    """Choose bounded cold boards and optionally emit only aggregate counts."""
    selected, seen = [], []
    counts = Counter(registered_boards=len(registry["boards"]))
    seed_keys = {b["key"] for b in seeded}
    projection = Projection()
    for platform in COLLECTORS:
        candidates = []
        for key, board in registry["boards"].items():
            if board["provider"] != platform:
                continue
            if projection.url(board["url"]) != board["url"]:
                counts["board_url_rejected"] += 1
                continue
            routes = sorted({e["kind"] for e in board["evidence"]
                             if e["kind"] in {"commoncrawl_url_index", "public_provider_sitemap"}})
            if not routes:
                counts["no_eligible_route"] += 1
                continue
            local = False
            evidence = []
            for item in board["evidence"]:
                source = item.get("url", "")
                if item["kind"] not in routes or projection.url(source) != source:
                    counts["evidence_source_rejected"] += 1
                    continue
                receipt = {field: item[field] for field in ("kind", "page", "pattern", "archive_timestamp")
                           if field in item}
                if "archive_timestamp" in receipt and not re.fullmatch(r"[0-9]{1,20}", receipt["archive_timestamp"]):
                    del receipt["archive_timestamp"]
                receipt["source"] = source
                observation = item.get("observed_url")
                if observation:
                    public_observation, error = _public_observation(observation, board["url"], projection)
                    if error:
                        counts[error] += 1
                        continue
                    receipt["observed_url"] = public_observation
                evidence.append(receipt)
                counts["evidence_accepted"] += 1
                local |= item.get("geography_hint") in {"dc_dmv", "in_market"}
            if not evidence:
                continue
            counts["qualified_boards"] += 1
            seen.append(dict(board_key=key, url=board["url"], seed_overlap=key in seed_keys,
                             routes=sorted({item["kind"] for item in evidence}), local_hint=local,
                             first_observed_at=board.get("first_seen_at"),
                             evidence=sorted(evidence, key=lambda e: json.dumps(e, sort_keys=True))))
            if key not in seed_keys:
                candidates.append((not local, sha(("issue-39-v1|" + key).encode()), key, board))
                counts["cold_candidates"] += 1
            else:
                counts["seed_overlaps"] += 1
        selected.extend(board_from_url(item[3]["url"]) for item in sorted(candidates)[:2])
    counts["selected_boards"] = len(selected)
    if screening is not None:
        screening.update({key: counts[key] for key in COLD_SCREENING_KEYS})
    chosen = {b["key"] for b in selected}
    return selected, [dict(row, selected=row["board_key"] in chosen)
                      for row in sorted(seen, key=lambda r: r["board_key"])]


class FixtureWire:
    """Consume a finite sequence of exact URLs and replies entirely in memory."""
    def __init__(self, script):
        self.script = iter(script)
        self.time = 0.0
        self.calls = []

    def sleep(self, seconds):
        self.time += seconds

    @contextmanager
    def open(self, url, seconds, timeout_reason):
        self.calls.append(url)
        expected, reply = next(self.script, (None, None))
        if expected != url:
            raise AssertionError(f"Unexpected fixture request: {url!r}; expected {expected!r}")
        if reply is None:
            raise FetchError("network_error")
        if isinstance(reply, KeyboardInterrupt):
            raise reply
        self.sleep(reply.connect)
        yield reply


class Budget:
    def __init__(self):
        self.counts = Counter()
        self.bytes = 0

    def reason(self, stage):
        if stage == "repeat" or sum(self.counts.values()) >= TOTAL_LIMIT or self.counts[stage] >= STAGE_LIMITS[stage]:
            return "request_budget"
        return "byte_budget" if self.bytes >= BYTE_LIMIT else ""

    def attempt(self, stage):
        if reason := self.reason(stage):
            raise FetchError(reason)
        self.counts[stage] += 1


class _GuardedTransport:
    """Shared collector interface. All HTTP attempts cross the same guard."""

    checks_robots = True

    def __init__(self, wire, terms):
        self.wire = wire
        self.terms = terms
        self.budget = Budget()
        self.requests = []
        self.blocked = {}
        self.robots_checked = {}
        self.robots_errors = {}
        self.access = []
        self.sitemaps = set()
        self.last_attempt = {}
        self.stage = "discovery"
        self.board = None
        self.deadline = 600
        self.details = set()
        self.board_requests = set()
        self.detail_limit = 0
        self.detail_count = 0
        self.privacy = Projection()
        self.halted = ""
        self.discovery_requests = set()
        self.collection = None
        self.index_pages = {}
        self.run_id = None

    def record(self, phase, event):
        """Fixture events are available in self.requests."""

    def global_stop_reason(self):
        if self.halted:
            return self.halted
        if self.wire.time >= 1800 or (self.stage == "discovery" and self.wire.time >= self.deadline):
            self.halted = "time_budget"
        if reason := self.budget.reason(self.stage):
            self.halted = self.halted or reason
        if self.halted:
            return self.halted
        return "time_budget" if self.wire.time >= self.deadline else ""

    def stage_for_board(self, stage, board, detail_limit=0):
        if (stage not in ("seeded", "cold") or board["provider"] not in COLLECTORS
                or board_from_url(board["url"]) != board or self.privacy.url(board["url"]) != board["url"]):
            raise ValueError("Invalid pilot board stage")
        self.global_stop_reason()  # Preserve a shared stop across stage transitions.
        self.stage, self.board = stage, board
        self.deadline = min(self.wire.time + 75, 1800)
        self.details.clear()
        self.board_requests.clear()
        self.detail_count = 0
        self.greenhouse_fallback = False
        self.detail_limit = min(detail_limit, 1) if stage == "seeded" and board["provider"] == "jazzhr" else 0

    def _route(self, host, path, params):
        if self.stage == "discovery":
            if host == FEED_HOST and re.fullmatch(r"/feeds/google/xml/[01]", path) and not params:
                return "sitemap"
            if host == INDEX_HOST and path == "/collinfo.json" and not params:
                return "catalog"
            if host == INDEX_HOST and self.collection and path == f"/{self.collection}-index":
                valid = {"url", "output", "filter", "fl", "limit", "pageSize"}
                expected = {"output": "json", "filter": "status:200", "fl": "url,timestamp,status",
                            "limit": 2500, "pageSize": 1}
                extra = set(params) - valid
                if (params.get("url") in {"job-boards.greenhouse.io/*", "jobs.lever.co/*"}
                        and all(params.get(k) == v for k, v in expected.items())):
                    if extra == {"showNumPages"} and params["showNumPages"] == "true":
                        return "index_count"
                    if (extra == {"page"} and type(params["page"]) is int
                            and params["page"] == self.index_pages.get(params["url"])):
                        return "index"
            return None
        if self.stage not in ("seeded", "cold") or not self.board:
            return None
        board = self.board
        public = urlsplit(board["url"])
        token = quote(board["token"], safe="._-")
        if board["provider"] == "greenhouse" and host == "boards-api.greenhouse.io":
            base = f"/v1/boards/{token}"
            if path == base and not params:
                return "metadata"
            if path == base + "/jobs" and params in ({}, {"content": "true"}):
                return "inventory"
        if board["provider"] == "lever":
            if host == "api.lever.co" and path == f"/v0/postings/{token}" and params in (
                {"mode": "json", "limit": 100, "skip": skip} for skip in (0, 100, 200)
            ):
                return "inventory"
            if (host, path) == (public.hostname, public.path) and not params:
                return "metadata"
        if board["provider"] == "jazzhr" and host == public.hostname and not params:
            if path in (public.path, public.path + "/"):
                return "inventory"
            if path.rstrip("/") in self.details and self.detail_count < self.detail_limit:
                return "detail"
        return None

    def _access(self, url, route, decision, **evidence):
        event = dict(url=url, route=route, decision=decision, stage=self.stage,
                     board_key=self.board["key"] if self.board else None, observed_at=stamp(), **evidence)
        self.access.append(event)
        self.record("source_access", event)

    def _terms(self, host, path, route):
        # A JazzHR detail permission covers only recognized links from this board's index.
        scope = "/apply/{posting}" if route == "detail" else path.rstrip("/") or "/"
        if not self.terms(host, scope, self.stage, self.board):
            decision = self.terms.reason(host, scope, self.stage) if hasattr(self.terms, "reason") else POLICY
            self._access(f"https://{host}{scope}", route, decision)
            raise FetchError(decision)

    def _robots(self, host, path, params, route):
        url = f"https://{host}{path}" + ("?" + urlencode(params) if params else "")
        if host in self.blocked:
            self._access(url, route, "host_stopped_after_denial", cause=self.blocked[host])
            raise FetchError("host_stopped_after_denial")
        if host not in self.robots_checked and host not in self.robots_errors:
            self._load_robots(host)
        evidence = {"robots_url": f"https://{host}/robots.txt"}
        if host in self.robots_errors:
            cause = self.robots_errors[host]
            self._access(url, route, "robots_unavailable", cause=cause, **evidence)
            raise FetchError(cause)
        rule = self.robots_checked[host]
        allowed, matched = rule.decision(url) if rule else (True, None)
        if not allowed:
            self._access(url, route, "robots_disallowed", rule=matched, **evidence)
            raise FetchError("robots_disallowed")
        if rule and rule.unsupported:
            self._access(url, route, "robots_unsupported", rule=rule.unsupported, **evidence)
            raise FetchError("robots_unsupported")
        self._access(url, route, "allowed", rule=matched, robots_missing=rule is None, **evidence)

    def _load_robots(self, host):
        try:
            body = self._read(host, "/robots.txt", {}, "robots", 100000, False, None)
        except FetchError as exc:
            if str(exc) in ("http_404", "http_410"):
                self.robots_checked[host] = None
            else:
                self.robots_errors[host] = str(exc)
            return
        self.robots_checked[host] = RobotsRules(body, USER_AGENT)
        if host == FEED_HOST:
            # The published sitemap can name HTTP. Only try the same host and
            # advertised path over HTTPS, through the normal route and terms checks.
            for page in re.findall(r"(?im)^\s*sitemap:\s*https?://app\.jazz\.co(/feeds/google/xml/[01])\s*$", body):
                self.sitemaps.add(f"https://{FEED_HOST}{page}")

    def get(self, url, params=None, max_bytes=8_000_000, kind="api", prefix=False, *,
            capture=None, redirect_filter=None):
        if reason := self.global_stop_reason():
            raise FetchError(reason)
        params = params or {}
        try:
            parsed = urlsplit(url)
        except ValueError:
            return self.reject_endpoint()
        if (not parsed.hostname or any(char.isspace() for char in url) or not PublicTransport.allowed(url)
                or parsed.netloc != parsed.hostname or parsed.query or parsed.fragment
                or parsed.scheme != "https" or self.privacy.url(url) != url):
            return self.reject_endpoint()
        host, path = parsed.hostname, parsed.path
        route = self._route(host, path, params)
        if route is None:
            # The existing Common Crawl helper probes its archive fallback
            # after an unusable sample. Skip that branch without dispatch.
            if (self.stage == "discovery" and host == "web.archive.org" and path == "/cdx/search/cdx"):
                raise FetchError("archive_fallback_disabled")
            return self.reject_endpoint()
        self._terms(host, path, route)
        self._robots(host, path, params, route)
        if route == "sitemap" and url not in self.sitemaps:
            self._access(url, route, "sitemap_not_advertised")
            raise FetchError("sitemap_not_advertised")
        request_key = (host, path.rstrip("/"), tuple(sorted(params.items())))
        requests = self.board_requests if self.board else self.discovery_requests
        if request_key in requests:
            raise FetchError("duplicate_request")
        if (self.board and self.board["provider"] == "greenhouse" and route == "inventory"
                and not params and not self.greenhouse_fallback):
            raise FetchError("unexpected_inventory_fallback")
        requests.add(request_key)
        try:
            body = self._read(host, path, params, route, max_bytes, prefix, capture, redirect_filter)
        except FetchError as exc:
            if params == {"content": "true"} and str(exc) == "response_too_large":
                self.greenhouse_fallback = True
            raise
        if route == "catalog":
            choices = [item["id"] for item in json.loads(body) if isinstance(item, dict)
                       and re.fullmatch(r"CC-MAIN-\d{4}-\d{2}", str(item.get("id", "")))]
            self.collection = max(choices, default=None)
        if route == "index_count":
            pages = json.loads(body).get("pages")
            if type(pages) is not int or not 1 <= pages <= 100000:
                raise FetchError("invalid_schema")
            pagekey = f"{self.collection}|{params['url']}"
            self.index_pages[params["url"]] = int(sha(pagekey.encode())[:12], 16) % pages
        if route == "inventory" and host.endswith(".applytojob.com") and self.detail_limit:
            for row in jazz_index(body, self.board, DC_MARKET):
                target = urlsplit(row["url"])
                if (target.hostname == host and PublicTransport.allowed(row["url"])
                        and self.privacy.url(row["url"]) == row["url"]):
                    self.details.add(target.path.rstrip("/"))
        return body

    def reject_endpoint(self):
        raise FetchError("unsafe_endpoint")

    def json(self, url, params=None, **kwargs):
        return json.loads(self.get(url, params, **kwargs))

    def _read(self, host, path, params, route, limit, prefix, capture, redirect_filter=None):
        query = urlencode(params)
        for hop in range(2):
            if host in self.blocked:
                raise FetchError("host_stopped_after_denial")
            if reason := self.global_stop_reason():
                raise FetchError(reason)
            rule = self.robots_checked.get(host)
            interval = max(2, rule.delay if rule else 0)
            remaining_wait = self.last_attempt.get(host, self.wire.time - interval) + interval - self.wire.time
            if self.wire.time + remaining_wait >= self.deadline:
                raise FetchError("stricter_crawl_delay_skip" if interval > 2 else "time_budget")
            self.wire.sleep(max(0, remaining_wait))
            if reason := self.global_stop_reason():
                raise FetchError(reason)
            self.budget.attempt(self.stage)
            if route == "detail" and not hop:
                self.detail_count += 1
            started = self.wire.time
            event = dict(stage=self.stage, host=host, route=route, redirect=bool(hop),
                         board_key=self.board["key"] if self.board else None, run_id=self.run_id,
                         elapsed=started, observed_at=stamp(), attempt=len(self.requests) + 1,
                         url=f"https://{host}{path}")
            self.requests.append(event)
            self.record("attempt", event)
            self.last_attempt[host] = started = self.wire.time
            url = event["url"] + ("?" + query if query else "")
            remaining = min(self.deadline, 1800) - self.wire.time
            timeout_reason = "time_budget" if remaining <= 25 else "request_timeout"
            retry_after = ""
            try:
                with self.wire.open(url, min(remaining, 25), timeout_reason) as reply:
                    event["status"] = reply.status
                    retry_after = reply.retry_after
                    if reply.status in DENIAL or reply.authenticate or reply.challenge:
                        self.blocked[host] = reply.status if reply.status in DENIAL else CHALLENGE
                        event["host_stop"] = self.blocked[host]
                        self._retry_after(event, reply.retry_after)
                    self._time_check(started, reply.connect, 5)
                    if reply.authenticate or reply.challenge:
                        raise FetchError(CHALLENGE)
                    if reply.status in (301, 302, 303, 307, 308):
                        path = self._redirect(reply.location, url, host, path, query, params, route,
                                              hop, redirect_filter)
                        continue
                    if reply.status != 200:
                        if reply.status in (404, 410, 503) and reply.media.lower().startswith("text/html"):
                            try:
                                self._body(reply, event, started, min(limit, 100000), False)
                            except FetchError as exc:
                                if str(exc) in (CHALLENGE, PRIVATE, "byte_budget", "time_budget", "request_timeout"):
                                    raise
                                if route == "robots" and str(exc) != "unexpected_content_type":
                                    raise
                        raise FetchError(f"http_{reply.status}")
                    body, data = self._body(reply, event, started, limit, prefix)
                self._capture(data, event, capture)
                return body
            except FetchError as exc:
                event["error"] = str(exc)
                if str(exc) == CHALLENGE:
                    event["host_stop"] = self.blocked[host] = CHALLENGE
                    self._retry_after(event, retry_after)
                if str(exc) in (PRIVATE, SOURCE_PRIVATE, "response_too_large", "byte_budget"):
                    event["withheld"] = True
                if str(exc) in (PRIVATE, "byte_budget") or (
                    str(exc) == "time_budget" and (self.stage == "discovery" or
                                                  self.wire.time >= 1800)
                ):
                    self.halted = str(exc)
                raise
            except KeyboardInterrupt:
                event["error"] = "interrupted"
                raise
            except Exception:
                event["error"] = "integrity_storage_or_unexpected_failure"
                raise
            finally:
                if route != "robots":
                    event["robots"] = ("missing" if self.robots_checked.get(host) is None else "allowed")
                    event["terms"] = "allowed"
                event["duration_seconds"] = self.wire.time - started
                self.record("result", event)

    def _redirect(self, location, url, host, path, query, params, route, hop, redirect_filter):
        try:
            target = urlsplit(urljoin(url, location))
        except ValueError:
            raise FetchError(REDIRECT) from None
        if (target.netloc == host and any(AUTH_PATH.fullmatch(unquote(part))
                                         for part in target.path.split("/"))):
            raise FetchError(CHALLENGE)
        if (not location or any(ord(char) < 33 for char in location) or hop or route == "robots"
                or target.scheme != "https" or target.netloc != host
                or target.fragment or target.query != query
                or target.path.rstrip("/") != path.rstrip("/") or target.path.endswith("//")
                or not PublicTransport.allowed(target.geturl())
                or self.privacy.url(target._replace(query="").geturl()) != target._replace(query="").geturl()
                or self._route(host, target.path, params) != route and route != "detail"
                or (redirect_filter and not redirect_filter(target.geturl()))):
            raise FetchError(REDIRECT)
        self._terms(host, target.path, route)
        self._robots(host, target.path, params, route)
        return target.path

    @staticmethod
    def _retry_after(event, retry):
        if re.fullmatch(r"[0-9]{1,9}", retry):
            event["retry_after_seconds"] = int(retry)
        elif len(retry) <= 128 and retry:
            try:
                date = parsedate_to_datetime(retry)
                event["retry_after_utc"] = timestamp(date.replace(tzinfo=date.tzinfo or timezone.utc))
            except (ValueError, OverflowError):
                event["retry_after"] = "uninterpretable"

    def _time_check(self, started, delay, phase_limit):
        if delay > phase_limit or self.wire.time - started > 25:
            raise FetchError("request_timeout")
        if self.wire.time >= min(self.deadline, 1800):
            raise FetchError("time_budget")

    def _body(self, reply, event, started, limit, prefix):
        data = bytearray()
        approved_limit = {"robots": 100000, "sitemap": 1_500_000, "catalog": 200000,
                          "index": 1_500_000, "index_count": 10000, "detail": 1_000_000}
        route = event["route"]
        default = 1_000_000 if event["host"].endswith(".applytojob.com") else 8_000_000
        limit = min(limit, approved_limit.get(route, default))
        prefix = prefix and route == "sitemap"
        if reply.encoding.strip().lower() not in ("", "identity"):
            raise FetchError("unexpected_content_encoding")
        if reply.content_length:
            if not re.fullmatch(r"[0-9]{1,18}", reply.content_length):
                raise FetchError("invalid_content_length")
            if not prefix and int(reply.content_length) > limit:
                event["withheld"] = True
                raise FetchError("response_too_large")
        chunks = iter(reply.chunks)
        while True:
            delay = 0
            remaining = BYTE_LIMIT - self.budget.bytes
            if remaining <= 0:
                raise FetchError("byte_budget")
            if reply.read is None:
                delay, chunk = next(chunks, (0, b""))
                self.wire.sleep(delay)
            else:
                size = min(8192, remaining, limit - len(data) + 1)
                try:
                    chunk = reply.read(size)
                except Exception:
                    # A failed raw read can consume part of its requested
                    # block before reporting an error. Charge conservatively.
                    event["unconfirmed_bytes_charged"] = size
                    self.budget.bytes += size
                    raise
            self.budget.bytes += len(chunk)
            event["bytes"] = event.get("bytes", 0) + len(chunk)
            self._time_check(started, delay, 10)
            if self.budget.bytes > BYTE_LIMIT:
                raise FetchError("byte_budget")
            if not chunk:
                break
            if len(data) + len(chunk) > limit:
                if not prefix:
                    raise FetchError("response_too_large")
                data.extend(chunk[:limit - len(data)])
                event["prefix_only"] = True
                break
            data.extend(chunk)
        if (reply.content_length and not event.get("prefix_only")
                and len(data) != int(reply.content_length)):
            raise FetchError("incomplete_response")
        try:
            body = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            if event.get("prefix_only") and exc.reason == "unexpected end of data":
                body = bytes(data[:exc.start]).decode("utf-8")
                del data[exc.start:]
            else:
                raise FetchError("invalid_encoding") from None
        media = reply.media.split(";", 1)[0].lower()
        is_html = media == "text/html" or body.lstrip("\ufeff \t\r\n").startswith("<") and route != "sitemap"
        # A known local secret or signed/private URL is a run-wide integrity
        # concern. Test it first, even when the same HTML also has form fields.
        if contains_configured_secret(body, self.privacy.secrets) or unsafe_body(body):
            raise FetchError(PRIVATE)
        jazz_inventory = (
            reply.status == 200 and media == "text/html" and route == "inventory"
            and self.board and self.board["provider"] == "jazzhr"
            and event["url"] in (self.board["url"], self.board["url"] + "/")
        )
        jazz_detail = (
            reply.status == 200 and media == "text/html" and route == "detail"
            and self.board and self.board["provider"] == "jazzhr"
            and urlsplit(event["url"]).hostname == urlsplit(self.board["url"]).hostname
            and urlsplit(event["url"]).path.rstrip("/") in self.details
        )
        if jazz_inventory or jazz_detail:
            # Check decoded structured state and relative URLs before capture.
            self._check_html_state(body, jazz_page_url=event["url"], public_detail=jazz_detail)
        try:
            rejection = _capture_rejection(
                data, public_inventory=jazz_inventory,
                detail_url=event["url"] if jazz_detail else None,
            ) if is_html else None
        except (TypeError, ValueError, RecursionError):
            raise FetchError("unreadable_public_html") from None
        if rejection == PRIVATE and re.search(r"(?i)<input\b[^>]*\btype\s*=\s*['\"]?password", body):
            raise FetchError(CHALLENGE)
        if rejection:
            # No raw body or page content is captured on this path. Quarantine
            # the HTML page without halting unrelated employer boards.
            raise FetchError(SOURCE_PRIVATE if rejection == PRIVATE else rejection)
        if re.search(r"(?i)\b(?:captcha|cf-chl-)[_-]?[a-z0-9-]*\b", body[:4096]) and is_html:
            raise FetchError(CHALLENGE)
        if is_html and not (jazz_inventory or jazz_detail) and TOKEN_ASSIGNMENT.search(body):
            raise FetchError(SOURCE_PRIVATE)
        if is_html and not (jazz_inventory or jazz_detail) and "\\" in body:
            self._check_html_state(body)
        if route == "robots":
            if media != "text/plain" or is_html:
                raise FetchError("unexpected_content_type")
        elif route == "sitemap":
            if media not in ("text/xml", "application/xml", "text/plain") or is_html:
                raise FetchError("unexpected_content_type")
        elif route == "detail" or event["host"] == "jobs.lever.co" or event["host"].endswith(".applytojob.com"):
            if media != "text/html":
                raise FetchError("unexpected_content_type")
        elif route == "index":
            if media not in ("application/json", "application/x-ndjson", "text/plain") or is_html:
                raise FetchError("unexpected_content_type")
        else:
            if media not in ("application/json", "text/plain", "application/octet-stream") or is_html:
                raise FetchError("unexpected_content_type")
            try:
                parsed = json.loads(body)
            except (ValueError, RecursionError):
                raise FetchError("invalid_json") from None
            if (route == "catalog" and not isinstance(parsed, list)
                    or route == "index_count" and not isinstance(parsed, dict)
                    or route == "metadata" and not isinstance(parsed, dict)):
                raise FetchError("invalid_schema")
            if route == "inventory":
                valid = isinstance(parsed, list) if event["host"] == "api.lever.co" else (
                    isinstance(parsed, dict) and isinstance(parsed.get("jobs"), list))
                if not valid:
                    raise FetchError("invalid_schema")
            self._check_json_privacy(parsed)
        return body, data

    def _check_html_state(self, body, *, jazz_page_url=None, public_detail=False):
        try:
            for node in Document(body).root.walk():
                if jazz_page_url:
                    for attr in ("href", "action", "src", "formaction"):
                        target = node.attrs.get(attr) or ""
                        if ("?" in target or public_detail and target.startswith("//")) and unsafe_body(
                                urljoin(jazz_page_url, target)):
                            raise FetchError(PRIVATE)
                media = node.attrs.get("type")
                if jazz_page_url:
                    media = (media or "").lower().split(";", 1)[0].strip()
                if node.tag == "script" and media in (
                    ("application/ld+json", "application/json") if jazz_page_url else ("application/ld+json",)
                ):
                    script = "".join(child for child in node.children if isinstance(child, str))
                    self._check_json_privacy(json.loads(script), public_jazzhr=bool(jazz_page_url),
                                             public_detail=public_detail and media == "application/json")
        except (ValueError, RecursionError):
            raise FetchError("unreadable_public_html") from None

    def _check_json_privacy(self, parsed, *, public_jazzhr=False, public_detail=False):
        pending = [parsed]
        while pending:
            value = pending.pop()
            if isinstance(value, list):
                pending.extend(value)
            if isinstance(value, dict):
                if any(item and (
                    (PRIVATE_FIELD.fullmatch(key) and not (
                        public_jazzhr and GENERIC_CSRF.fullmatch(key) and isinstance(item, str)
                    )) or (public_jazzhr and JAZZHR_JSON_PERSONAL_FIELD.fullmatch(key))
                    or (public_detail and JAZZHR_DETAIL_PRIVATE_FIELD.fullmatch(key))
                ) for key, item in value.items()):
                    raise FetchError(PRIVATE)
                pending.extend(value)
                pending.extend(value.values())
            if isinstance(value, str) and (unsafe_body(value) or contains_configured_secret(value, self.privacy.secrets)):
                raise FetchError(PRIVATE)

    def _capture(self, data, event, capture):
        route = event["route"]
        if capture and route in {"inventory", "detail", "metadata"}:
            try:
                disposition = capture(bytes(data))
            except Exception as exc:
                raise RuntimeError("Pilot evidence callback failed") from exc
            if disposition:
                event["withheld"] = True
                # A source-local privacy classifier rejects suspect HTML; an
                # actual secret echo from an evidence callback remains global.
                raise FetchError(SOURCE_PRIVATE if disposition == PRIVATE else PRIVATE)
            event["sha256"] = sha(data)
        elif route in ("robots", "sitemap", "catalog", "index", "index_count"):
            event["sha256"] = sha(data)


class PilotTransport(_GuardedTransport):
    """Offline entry point, restricted to in-memory replies."""

    def __init__(self, wire, terms):
        if type(wire) is not FixtureWire:
            raise TypeError("Only an in-memory fixture wire is supported")
        allowed = frozenset(terms)
        super().__init__(wire, lambda host, path, stage, board: (host, path) in allowed)


class LivePilotTransport(_GuardedTransport):
    def __init__(self, terms, journal):
        super().__init__(HTTPSWire(), terms)
        self.journal = journal

    def record(self, phase, event):
        self.journal(phase, event)

    def reject_endpoint(self):
        # An unapproved URL must never be requested, but one adapter's bad
        # endpoint should not cancel independent, correctly scoped boards.
        self.journal("policy_skip", {
            "reason": "unsafe_endpoint",
            "stage": self.stage,
            "board_key": self.board["key"] if self.board else None,
        })
        return super().reject_endpoint()

    def close(self):
        self.wire.close()

def run_fixture(data_dir, seeded, transport, *, code_sha, config_sha):
    """Exercise both arms against a synthetic script and produce the real v1.1 export."""
    if type(transport) is not PilotTransport or type(transport.wire) is not FixtureWire:
        raise TypeError("Only fixture transport is supported")
    data = Path(data_dir)
    keys = {board["key"] for board in seeded}
    if len(seeded) > 24 or len(keys) != len(seeded) or any(
        board_from_url(board["url"]) != board or board["provider"] not in COLLECTORS
        or Projection().url(board["url"]) != board["url"] for board in seeded
    ):
        raise ValueError("Invalid fixture cohort")
    if data.exists() and any(data.iterdir()):
        raise ValueError("Use a fresh fixture store")
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    from tools.pilot39_run import BOARD_RETENTION, Journal, execute, purge_diagnostic_bodies, write_json

    run = data / "run"
    run.mkdir(mode=0o700)
    provenance = dict(code_sha=code_sha, config_sha256=config_sha, raw_retention=BOARD_RETENTION)
    write_json(run / "provenance.json", provenance)
    try:
        with (run / "operations.jsonl").open("x", encoding="utf-8") as stream:
            snapshot = execute(data, run, seeded, transport, Journal(stream), provenance)
    finally:
        purge_diagnostic_bodies(data)
    report = json.loads((run / "run-manifest.json").read_text())
    report["polls"] = json.loads((run / "board-polls.json").read_text())
    report["candidates"] = [json.loads(line) for line in
                            (run / "candidate-ledger.jsonl").read_text().splitlines()]
    report["requests"] = transport.requests
    return snapshot, report

def main():
    from tools.pilot39_run import cli

    cli()


if __name__ == "__main__":
    main()
