"""Issue #39 offline precheck and synthetic replay. No live HTTP implementation."""
import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from career_engine.ats import FetchError, PublicTransport, board_from_url, stamp
from career_engine.ats_registry import discover_commoncrawl, empty_registry
from career_engine.export import export_snapshot
from career_engine.extract import USER_AGENT
from career_engine.jazzhr import jazz_index
from career_engine.markets import DC_MARKET
from career_engine.public_sources import discover_jazz_sitemap
from career_engine.relabel import relabel
from career_engine.store import Store
from career_engine.store.collectors import collect_greenhouse, collect_lever
from career_engine.store.jazzhr import _capture_rejection, collect_jazzhr
from career_engine.store.privacy import Projection, SECRET_NAME, contains_configured_secret

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
PRIVATE = "private_page_state"
REDIRECT = "unsafe_redirect"
POLICY = "terms_unverified_or_restricted"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def check_cohort(urls):
    boards = [board_from_url(url) for url in urls]
    if (len(urls) != 24 or len(set(urls)) != 24 or sha(("\n".join(urls) + "\n").encode()) != COHORT_SHA
            or any(not board or board["url"] != url or board["provider"] not in COLLECTORS
                   or Projection().url(url) != url for url, board in zip(urls, boards))):
        raise ValueError("Cohort does not match the frozen public board list")
    return boards


def select_cold(registry, seeded):
    selected, seen = [], []
    seed_keys = {b["key"] for b in seeded}
    for platform in COLLECTORS:
        candidates = []
        for key, board in registry["boards"].items():
            if board["provider"] != platform or Projection().url(board["url"]) != board["url"]:
                continue
            routes = sorted({e["kind"] for e in board["evidence"]
                             if e["kind"] in {"commoncrawl_url_index", "public_provider_sitemap"}})
            if not routes:
                continue
            local = any(e.get("geography_hint") in {"dc_dmv", "in_market"} for e in board["evidence"])
            evidence = []
            for item in board["evidence"]:
                source = item.get("url", "")
                if item["kind"] not in routes or Projection().url(source) != source:
                    continue
                receipt = {field: item[field] for field in ("kind", "page", "pattern", "archive_timestamp")
                           if field in item}
                if "archive_timestamp" in receipt and not re.fullmatch(r"[0-9]{1,20}", receipt["archive_timestamp"]):
                    del receipt["archive_timestamp"]
                receipt["source"] = source
                evidence.append(receipt)
            seen.append(dict(board_key=key, url=board["url"], seed_overlap=key in seed_keys,
                             routes=routes, local_hint=local,
                             evidence=sorted(evidence, key=lambda e: json.dumps(e, sort_keys=True))))
            if key not in seed_keys:
                candidates.append((not local, sha(("issue-39-v1|" + key).encode()), key, board))
        selected.extend(board_from_url(item[3]["url"]) for item in sorted(candidates)[:2])
    chosen = {b["key"] for b in selected}
    return selected, [dict(row, selected=row["board_key"] in chosen)
                      for row in sorted(seen, key=lambda r: r["board_key"])]


@dataclass(frozen=True)
class Reply:
    """A synthetic response. Delays model connect time and time between chunks."""
    status: int = 200
    media: str = "application/json"
    chunks: tuple = ()
    location: str = ""
    connect: float = 0
    retry_after: str = ""


class FixtureWire:
    """Consume a finite sequence of exact URLs and replies entirely in memory."""
    def __init__(self, script):
        self.script = iter(script)
        self.time = 0.0
        self.calls = []

    def sleep(self, seconds):
        self.time += seconds

    def get(self, url):
        self.calls.append(url)
        expected, reply = next(self.script, (None, None))
        if expected != url:
            raise AssertionError(f"Unexpected fixture request: {url!r}; expected {expected!r}")
        if reply is None:
            raise FetchError("network_error")
        if isinstance(reply, KeyboardInterrupt):
            raise reply
        self.sleep(reply.connect)
        return reply


class Budget:
    def __init__(self):
        self.counts = Counter()
        self.bytes = 0

    def reason(self, stage):
        if sum(self.counts.values()) >= TOTAL_LIMIT or self.counts[stage] >= STAGE_LIMITS[stage]:
            return "request_budget"
        return "byte_budget" if self.bytes >= BYTE_LIMIT else ""

    def attempt(self, stage):
        if reason := self.reason(stage):
            raise FetchError(reason)
        self.counts[stage] += 1


class PilotTransport(PublicTransport):
    """Use collector transport conventions with a stricter fixture-only GET boundary."""
    def __init__(self, wire, terms):
        if type(wire) is not FixtureWire:
            raise TypeError("Only an in-memory fixture wire is supported")
        # Do not construct a requests.Session, including through the base initializer.
        self.wire = wire
        self.terms = frozenset(terms)  # exact (hostname, path); approved for capture and retention
        self.budget = Budget()
        self.requests = []
        self.blocked = {}
        self.robots = {}
        self.robots_documents = {}
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

    def global_stop_reason(self):
        if self.halted:
            return self.halted
        if self.wire.time >= min(self.deadline, 1800):
            return "time_budget"
        return self.budget.reason(self.stage)

    def stage_for_board(self, stage, board, detail_limit=0):
        if stage not in ("seeded", "cold") or board["provider"] not in COLLECTORS:
            raise ValueError("Invalid pilot board stage")
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
            if host == INDEX_HOST and re.fullmatch(r"/CC-MAIN-\d{4}-\d{2}-index", path):
                valid = {"url", "output", "filter", "fl", "limit", "pageSize"}
                expected = {"output": "json", "filter": "status:200", "fl": "url,timestamp,status",
                            "limit": 2500, "pageSize": 1}
                extra = set(params) - valid
                if (params.get("url") in {"job-boards.greenhouse.io/*", "jobs.lever.co/*"}
                        and all(params.get(k) == v for k, v in expected.items())):
                    if extra == {"showNumPages"} and params["showNumPages"] == "true":
                        return "index_count"
                    if extra == {"page"} and type(params["page"]) is int and 0 <= params["page"] < 100000:
                        return "index"
            return None
        if self.stage not in ("seeded", "cold") or not self.board:
            return None
        board = self.board
        public = urlsplit(board["url"])
        token = board["token"]
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
            if path.rstrip("/") == public.path:
                return "inventory"
            if path.rstrip("/") in self.details and self.detail_count < self.detail_limit:
                return "detail"
        return None

    def _terms(self, host, path, route):
        # A JazzHR detail permission covers only recognized links from this board's index.
        scope = "/apply/{posting}" if route == "detail" else path.rstrip("/") or "/"
        if (host, scope) not in self.terms:
            raise FetchError(POLICY)

    def _robots(self, host, path, params):
        if host not in self.robots:
            self._load_robots(host)
        rule = self.robots[host]
        url = f"https://{host}{path}" + ("?" + urlencode(params) if params else "")
        if not rule or (rule is not True and not rule.can_fetch(USER_AGENT, url)):
            raise FetchError("robots_disallowed_or_unavailable")
        if rule is not True and (rule.crawl_delay(USER_AGENT) or 0) > 1:
            raise FetchError("stricter_crawl_delay_skip")

    def _load_robots(self, host):
        self.robots[host] = False
        try:
            body = self._read(host, "/robots.txt", {}, "robots", 100000, False, None)
        except FetchError as exc:
            # The reviewed exact-route terms gate is required even with no robots file.
            if str(exc) in ("http_404", "http_410"):
                self.robots[host] = True
                self.robots_documents[host] = ""
                return
            raise
        rule = RobotFileParser()
        for line in body.splitlines():
            directive = line.split("#", 1)[0].strip()
            if not directive:
                continue
            key, sep, value = directive.partition(":")
            key, value = key.lower().strip(), value.strip()
            if (not sep or key not in {"user-agent", "allow", "disallow", "crawl-delay", "sitemap"}
                    or key in {"allow", "disallow"} and (value and not re.fullmatch(r"/[A-Za-z0-9/._~-]*", value))
                    or key == "crawl-delay" and not re.fullmatch(r"[0-9]+", value)
                    or key == "user-agent" and not value):
                raise FetchError("robots_unsupported")
        # RobotFileParser alone uses first-match rather than longest-match precedence.
        # Refuse mixed allow/deny files until a reviewed parser can establish precedence.
        if re.search(r"(?im)^\s*allow\s*:", body) and re.search(r"(?im)^\s*disallow\s*:\s*/", body):
            raise FetchError("robots_unsupported")
        rule.parse(body.splitlines())
        if sum(entry.applies_to(USER_AGENT) for entry in rule.entries) > 1:
            raise FetchError("robots_unsupported")
        self.robots[host] = rule
        self.robots_documents[host] = body
        if host == FEED_HOST:
            self.sitemaps.update(re.findall(r"(?im)^\s*sitemap:\s*(https://app\.jazz\.co/feeds/google/xml/[01])\s*$", body))

    def get(self, url, params=None, max_bytes=8_000_000, kind="api", prefix=False, *,
            capture=None, redirect_filter=None):
        params = params or {}
        parsed = urlsplit(url)
        if (not parsed.hostname or not PublicTransport.allowed(url)
                or parsed.netloc != parsed.hostname or parsed.query or parsed.fragment
                or parsed.scheme != "https" or self.privacy.url(url) != url):
            raise FetchError("unsafe_endpoint")
        host, path = parsed.hostname, parsed.path
        if path == "/robots.txt" and not params:
            # Existing collectors request robots themselves. Authorize only their
            # own current HTML host, or the exact JazzHR feed discovery host.
            if not (self.stage == "discovery" and host == FEED_HOST
                    and any((host, f"/feeds/google/xml/{i}") in self.terms for i in (0, 1))
                    or self.board and host == urlsplit(self.board["url"]).hostname
                    and (host, urlsplit(self.board["url"]).path) in self.terms):
                raise FetchError(POLICY)
            if host not in self.robots:
                self._load_robots(host)
            # Legacy robots callers do their own parsing. The stricter cached
            # rules are already installed and every actual route is rechecked.
            if not self.robots[host]:
                raise FetchError("robots_disallowed_or_unavailable")
            return self.robots_documents[host]
        route = self._route(host, path, params)
        if route is None:
            raise FetchError("unsafe_endpoint")
        self._terms(host, path, route)
        self._robots(host, path, params)
        if route == "sitemap" and url not in self.sitemaps:
            raise FetchError("sitemap_not_advertised")
        if self.board:
            request_key = (host, path.rstrip("/"), tuple(sorted(params.items())))
            if request_key in self.board_requests:
                raise FetchError("duplicate_request")
            if (self.board["provider"] == "greenhouse" and route == "inventory"
                    and not params and not self.greenhouse_fallback):
                raise FetchError("unexpected_inventory_fallback")
            self.board_requests.add(request_key)
        try:
            body = self._read(host, path, params, route, max_bytes, prefix, capture, redirect_filter)
        except FetchError as exc:
            if params == {"content": "true"} and str(exc) == "response_too_large":
                self.greenhouse_fallback = True
            raise
        if route == "inventory" and host.endswith(".applytojob.com") and self.detail_limit:
            for row in jazz_index(body, self.board, DC_MARKET):
                target = urlsplit(row["url"])
                if target.hostname == host and self.privacy.url(row["url"]) == row["url"]:
                    self.details.add(target.path.rstrip("/"))
        return body

    def _read(self, host, path, params, route, limit, prefix, capture, redirect_filter=None):
        for hop in range(2):
            if host in self.blocked:
                raise FetchError("host_stopped_after_denial")
            self.wire.sleep(max(0, self.last_attempt.get(host, -2) + 2 - self.wire.time))
            if reason := self.global_stop_reason():
                raise FetchError(reason)
            self.budget.attempt(self.stage)
            if route == "detail" and not hop:
                self.detail_count += 1
            self.last_attempt[host] = started = self.wire.time
            event = dict(stage=self.stage, host=host, route=route, redirect=bool(hop),
                         board_key=self.board["key"] if self.board else None, elapsed=started, observed_at=stamp(),
                         url=f"https://{host}{path}")
            self.requests.append(event)
            url = f"https://{host}{path}" + ("?" + urlencode(params) if params else "")
            try:
                reply = self.wire.get(url)
                event["status"] = reply.status
                if reply.status in DENIAL:
                    retry = reply.retry_after
                    self.blocked[host] = reply.status
                    if re.fullmatch(r"[0-9]{1,9}", retry):
                        event["retry_after_seconds"] = int(retry)
                self._time_check(started, reply.connect, 5)
                if reply.status in (301, 302, 303, 307, 308):
                    target = urlsplit(urljoin(url, reply.location))
                    if (hop or route == "robots" or target.scheme != "https" or target.netloc != host
                            or target.fragment or target.query != urlsplit(url).query
                            or target.path.rstrip("/") != path.rstrip("/")
                            or not PublicTransport.allowed(target.geturl())
                            or (route != "detail" and self._route(host, target.path, params) != route)
                            or (redirect_filter and not redirect_filter(target.geturl()))):
                        raise FetchError(REDIRECT)
                    self._terms(host, target.path, route)
                    self._robots(host, target.path, params)
                    path = target.path
                    continue
                if reply.status != 200:
                    raise FetchError(f"http_{reply.status}")
                return self._body(reply, event, started, limit, prefix, capture)
            except FetchError as exc:
                event["error"] = str(exc)
                if str(exc) == CHALLENGE:
                    self.blocked[host] = CHALLENGE
                if str(exc) == PRIVATE:
                    self.halted = PRIVATE
                raise

    def _time_check(self, started, delay, phase_limit):
        if delay > phase_limit or self.wire.time - started > 25:
            raise FetchError("request_timeout")
        if self.wire.time >= min(self.deadline, 1800):
            raise FetchError("time_budget")

    def _body(self, reply, event, started, limit, prefix, capture):
        data = bytearray()
        approved_limit = {"robots": 100000, "sitemap": 1_500_000, "catalog": 200000,
                          "index": 1_500_000, "index_count": 10000, "detail": 1_000_000}
        route = event["route"]
        default = 1_000_000 if event["host"].endswith(".applytojob.com") else 8_000_000
        limit = min(limit, approved_limit.get(route, default))
        for delay, chunk in reply.chunks:
            self.wire.sleep(delay)
            self.budget.bytes += len(chunk)
            event["bytes"] = event.get("bytes", 0) + len(chunk)
            self._time_check(started, delay, 10)
            if self.budget.bytes > BYTE_LIMIT:
                raise FetchError("byte_budget")
            data.extend(chunk)
            if len(data) > limit:
                if not prefix or event["route"] != "sitemap":
                    raise FetchError("response_too_large")
                del data[limit:]
                event["prefix_only"] = True
                break
        try:
            body = data.decode("utf-8")
        except UnicodeError:
            raise FetchError("invalid_encoding") from None
        media = reply.media.split(";", 1)[0].lower()
        html = media == "text/html" or body.lstrip().lower().startswith(("<!doctype html", "<html", "<title"))
        rejection = _capture_rejection(data) if html else None
        if rejection or re.search(r"(?i)\b(?:captcha|cf-chl-)[_-]?[a-z0-9-]*\b", body[:4096]) and html:
            raise FetchError(rejection or CHALLENGE)
        if (contains_configured_secret(body, self.privacy.secrets)
                or html and re.search(r"(?i)\b(?:token|password|sessionid|credential)\s*[\"']?\s*[:=]", body)):
            raise FetchError(PRIVATE)
        if route == "robots":
            if media != "text/plain" or html:
                raise FetchError("unexpected_content_type")
        elif route == "sitemap":
            if media not in ("text/xml", "application/xml", "text/plain") or html:
                raise FetchError("unexpected_content_type")
        elif route == "detail" or event["host"] == "jobs.lever.co" or event["host"].endswith(".applytojob.com"):
            if media != "text/html":
                raise FetchError("unexpected_content_type")
        elif route == "index":
            if media not in ("application/json", "application/x-ndjson", "text/plain") or html:
                raise FetchError("unexpected_content_type")
        else:
            if media not in ("application/json", "text/plain", "application/octet-stream") or html:
                raise FetchError("unexpected_content_type")
            try:
                parsed = json.loads(body)
            except ValueError:
                raise FetchError("invalid_json") from None
            if route == "inventory":
                valid = isinstance(parsed, list) if event["host"] == "api.lever.co" else (
                    isinstance(parsed, dict) and isinstance(parsed.get("jobs"), list))
                if not valid:
                    raise FetchError("invalid_schema")
            # Detect escaped credential values and structured private state before capture.
            if contains_configured_secret(json.dumps(parsed, ensure_ascii=False), self.privacy.secrets):
                raise FetchError(PRIVATE)
            pending = [parsed]
            while pending:
                value = pending.pop()
                if isinstance(value, list):
                    pending.extend(value)
                if isinstance(value, dict):
                    if any(SECRET_NAME.fullmatch(k) and v for k, v in value.items()):
                        raise FetchError(PRIVATE)
                    pending.extend(value.values())
        if capture and not event.get("prefix_only"):
            disposition = capture(bytes(data))
            if disposition:
                event["withheld"] = True
            else:
                event["sha256"] = sha(data)
        elif route in ("sitemap", "catalog", "index", "index_count"):
            event["sha256"] = sha(data)
        return body


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
    registry = empty_registry()
    queries = discover_commoncrawl(transport, registry, query_budget=2)
    feeds = discover_jazz_sitemap(transport, registry, page_budget=2, market=DC_MARKET)
    cold, candidates = select_cold(registry, seeded)
    with (data / "fixture-plan.json").open("x", encoding="utf-8") as stream:
        json.dump(dict(candidates=candidates, seeded=[board["key"] for board in seeded]),
                  stream, sort_keys=True, indent=2)
    ledger = []
    with Store.in_data_dir(data) as store:
        for arm, boards in (("seeded", seeded), ("cold", cold)):
            run_id = store.begin_run(code_sha=code_sha, config_sha=config_sha, arm=arm, trigger="fixture")
            for slot, board in enumerate(boards):
                entry = dict(arm=arm, board_key=board["key"], run_id=run_id)
                ledger.append(entry)
                transport.stage_for_board(arm, board, int(slot in DETAIL_SLOTS))
                if reason := transport.global_stop_reason():
                    entry.update(attempted=False, reason=reason)
                    continue
                before = len(transport.requests)
                args = (store, run_id, transport, board)
                if board["provider"] == "jazzhr":
                    _, stats = collect_jazzhr(*args, detail_budget=transport.detail_limit, market=DC_MARKET)
                else:
                    _, stats = COLLECTORS[board["provider"]](*args, max_pages=3, detail_budget=0, market=DC_MARKET)
                poll = store.connection.execute("""
                    SELECT p.poll_id, p.status, p.received, p.declared_total, p.unrepresented, p.raw_sha
                    FROM poll p JOIN channel c USING (channel_id)
                    WHERE p.run_id = ? AND c.channel_key = ?
                """, (run_id, board["key"])).fetchone()
                attempts = transport.requests[before:]
                receipts = store.connection.execute("""
                    SELECT disposition, raw_sha, sightings FROM poll_inventory
                    WHERE poll_id = ? ORDER BY ordinal
                """, (poll["poll_id"],))
                entry["inventory_receipts"] = [dict(receipt) for receipt in receipts]
                entry.update(dict(poll), attempted=any(e["route"] == "inventory" for e in attempts),
                             request_count=len(attempts), errors=stats["errors"],
                             cap=stats["capped"], inventory=stats["inventory"])
            store.end_run(run_id)
        if transport.halted:
            raise ValueError("Fixture privacy stop blocks export")
        relabel(store, "geo", "gazetteer-dc/1")
        snapshot = export_snapshot(store, data / "exports", code_sha=code_sha)
    report = dict(candidates=candidates, polls=ledger, requests=transport.requests,
                  stage_attempts=dict(transport.budget.counts), bytes=transport.budget.bytes,
                  code_sha=code_sha, config_sha=config_sha, snapshot_id=snapshot.name,
                  snapshot_manifest_sha256=sha((snapshot / "manifest.json").read_bytes()),
                  discovery_status=[item["status"] for item in queries + feeds])
    with (data / "fixture-ledger.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, sort_keys=True, indent=2)
    return snapshot, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("precheck",))
    parser.add_argument("--cohort", required=True, type=Path, help="Private file with 24 board URLs, one per line")
    args = parser.parse_args()
    boards = check_cohort(args.cohort.read_text(encoding="utf-8").splitlines())
    print(json.dumps({"cohort_sha256": COHORT_SHA, "board_count": len(boards),
                      "platforms": Counter(b["provider"] for b in boards)}))


if __name__ == "__main__":
    main()
