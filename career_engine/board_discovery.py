"""Resumable public board discovery. Each worker handles one scheduled HTTP request."""
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, wait
from dataclasses import dataclass, field
import json
import math
import os
from sqlite3 import Row
from threading import Thread
import time
from typing import Callable
from urllib.parse import urlsplit
from uuid import uuid4

from requests.utils import requote_uri

from career_engine.ats import board_from_url
from career_engine.board_catalogs import CATALOG_CAMPAIGN
from career_engine.board_discovery_http import ORIGINS, USER_AGENT, Response, Transport, access_stop, retry_seconds
from career_engine.board_discovery_state import DiscoveryState, utc
from career_engine.board_indexes import INDEX_URLS, Plan, board_probe_url, index_url, parse_page
from tools.pilot39_robots import RobotsRules

# Default start-to-start gaps per origin. Robots may increase the gap.
DELAYS = {
    "web.archive.org": 1, "urlscan.io": 2, "index.commoncrawl.org": 1,
    "boards-api.greenhouse.io": .25, "api.lever.co": 1, "api.eu.lever.co": 1, "api.ashbyhq.com": .25,
}
ROBOTS_TTL = 86_400
RETRYABLE = {"candidate", "deferred", "failed"}


@dataclass(frozen=True)
class Limits:
    requests: int
    pages: int
    concurrency: int = 8
    bytes: int = 256_000_000
    response_bytes: int = 4_000_000
    seconds: float = 3600
    timeout: float = 30
    idle_seconds: float = 5
    retry_delay: float = 60
    origin_delays: dict[str, float] = field(default_factory=dict)
    origin_concurrency: dict[str, int] = field(default_factory=dict)

    def __post_init__(self):
        if min(self.requests, self.pages, self.concurrency, self.bytes, self.response_bytes) < 1:
            raise ValueError("Request, page, worker, and byte limits must be positive")
        times = (self.seconds, self.timeout, self.idle_seconds, self.retry_delay, *self.origin_delays.values())
        if any(not math.isfinite(t) or t < 0 for t in times) or min(self.seconds, self.timeout, self.retry_delay) == 0:
            raise ValueError("Invalid discovery time limit or delay")
        if (self.origin_delays.keys() | self.origin_concurrency.keys()) - DELAYS.keys():
            raise ValueError("Unknown discovery origin")
        if any(slots < 1 for slots in self.origin_concurrency.values()):
            raise ValueError("Origin concurrency must be positive")


@dataclass(frozen=True)
class Work:
    method: str
    url: str
    stream: Row | None = None
    variant: Row | None = None
    robots: bool = False

    @property
    def origin(self):
        return urlsplit(self.url).hostname


@dataclass
class Dispatch:
    work: Work
    cap: int
    deadline: float
    pacing: float
    robots_expires: float
    started: Future = field(default_factory=Future)
    result: Future = field(default_factory=Future)
    start_saved: bool = False


class Discovery:
    def __init__(self, state: DiscoveryState, transport: Transport, campaign: str, plan: Plan | None,
                 limits: Limits, *, mode="both", denied_prefixes=(), denied_methods=(),
                 replace_access_policy=False, clock=time.time,
                 monotonic=time.monotonic, sleep=time.sleep, stopping: Callable[[], bool] = lambda: False):
        if mode not in {"both", "index", "validate"}:
            raise ValueError("Invalid discovery mode")
        if campaign == CATALOG_CAMPAIGN and (plan is not None or mode != "validate"):
            raise ValueError("Public catalogs only support validation")
        if plan is None and campaign != CATALOG_CAMPAIGN:
            raise ValueError("Index campaigns require a discovery plan")
        routes = [("*", prefix) for prefix in denied_prefixes] + list(denied_methods)
        for method, prefix in routes:
            if (method not in {"*", "HEAD", "GET"} or
                    not prefix.startswith(tuple(f"https://{host}/" for host in ORIGINS)) or
                    "#" in prefix or any(c.isspace() or ord(c) < 32 for c in prefix)):
                raise ValueError("Deny rules need an HTTPS discovery route and, if specified, HEAD or GET")
        self.state, self.transport, self.campaign, self.plan = state, transport, campaign, plan
        self.limits, self.mode = limits, mode
        self.denied_routes = tuple(sorted({(method, requote_uri(prefix)) for method, prefix in routes}))
        self.replace_access_policy = replace_access_policy
        self.clock, self.monotonic, self.sleep, self.stopping = clock, monotonic, sleep, stopping
        self.invocation = uuid4().hex
        self.origins = deque(ORIGINS)
        self.streams = {origin: deque() for origin in ORIGINS}
        self.robots = {}
        self.robots_unavailable = set()
        self.cooldown = {}
        self.dispatch_closed = False
        self.suspended = set()
        self.attempt_checkpoint = 0
        self.inflight = {}
        self.abandoned = set()
        self.next_start = {}
        self.requests = self.pages = self.bytes = self.reserved = 0

    def _work(self, origin, active, robots_expired):
        lanes = self.streams[origin]
        active_streams = {work.stream["id"] for work in active if work.stream is not None}
        if self.mode != "validate" and self.pages < self.limits.pages:
            for identifier in lanes:
                stream = self.state.stream(identifier)
                robots_denial = (robots_expired and stream["status"] == "denied" and
                                 stream["reason"].startswith("robots: "))
                if ((stream["status"] not in RETRYABLE and not robots_denial) or
                        identifier in self.suspended or identifier in active_streams):
                    continue
                if stream["source"] == "commoncrawl" and not self.state.fallback_eligible(stream):
                    continue
                url = index_url(self.plan, stream["source"], stream["host"], stream["cursor"], stream["total"])
                return Work("GET", url, stream=stream)
        if self.mode != "index":
            active_keys = {work.variant["board_key"] for work in active if work.variant is not None}
            for variant in self.state.variants(
                origin, self.attempt_checkpoint,
                catalog_only=self.campaign == CATALOG_CAMPAIGN,
                robots_expired=robots_expired,
            ):
                if variant["board_key"] not in active_keys:
                    return Work(variant["method"], board_probe_url(board_from_url(variant["url"])), variant=variant)
        return None

    def _finish(self, work, status, reason, response, now, *, method=None):
        if work.stream is not None:
            self.state.set_stream(work.stream, status, reason)
            self.suspended.add(work.stream["id"])
        if work.variant is not None:
            self.state.set_variant(work.variant, status, reason, method or work.method, utc(now))
        self._attempt(work, status, reason, response, now, keep_turn=method is not None)

    def _attempt(self, work, status, reason, response, now, *, keep_turn=False):
        # Stream ID, page digest and campaign plan identify the query. Do not log opaque cursors.
        endpoint = work.url.split("?", 1)[0] if work.stream is not None else work.url
        self.state.attempt(self.invocation, self.campaign, work.method, endpoint, status, reason,
                           response, utc(now), stream=work.stream,
                           variant=work.variant["id"] if work.variant else None, keep_turn=keep_turn)

    def _policy(self, work, rules):
        url = requote_uri(work.url)
        if any(method in ("*", work.method) and url.startswith(prefix) for method, prefix in self.denied_routes):
            return "operator_policy"
        if rules is not None:
            allowed, _ = rules.decision(url)
            if not allowed:
                return "robots: disallow"
        return ""

    def _cache_robots(self, origin, rules, now):
        expires = now + ROBOTS_TTL
        self.robots[origin] = (expires, rules)
        self.state.save_robots(origin, expires, rules)

    def _rules(self, origin):
        expires, rules = self.robots.get(origin, (0, None))
        return rules if self.clock() < expires else None

    def _robots_result(self, work, response, now):
        code = response.status
        reason = response.error
        if not reason and code == 200:
            try:
                body = response.body.decode("utf-8")
                if body.lstrip("\ufeff \t\r\n").startswith("<"):
                    raise ValueError("HTML robots response")
                rules = RobotsRules(body, USER_AGENT)
                if rules.unsupported:
                    reason = "robots_pacing_unsupported"
                else:
                    self._cache_robots(work.origin, rules, now)
                    self.state.set_origin(work.origin, now + rules.delay, "")
            except (UnicodeDecodeError, ValueError):
                reason = "robots_unverified"
        elif not reason and code in {404, 410}:
            self._cache_robots(work.origin, RobotsRules("", USER_AGENT), now)
        else:
            reason = reason or f"robots_http_{code}"
        if reason:
            self.robots_unavailable.add(work.origin)
        self._attempt(work, "deferred" if reason else "allowed",
                      reason or ("robots_absent" if code != 200 else "robots_checked"), response, now)

    def _page_result(self, work, response, now):
        stream = work.stream
        try:
            if stream["source"] == "commoncrawl" and stream["total"] is None:
                data = json.loads(response.body)
                total = data.get("pages") if isinstance(data, dict) else None
                if type(total) is not int or not 0 <= total <= 2**63 - 1:
                    raise ValueError("commoncrawl_page_count")
                status = "candidate" if total else "empty"
                self.state.set_stream(stream, status, "page_count_recorded", cursor="0", total=total)
            else:
                page = parse_page(stream["source"], response.body, stream["cursor"], stream["total"])
                if page.cursor is not None and self.state.seen_cursor(stream, page.cursor):
                    self.state.record_page(stream, page, utc(now), advance=False)
                    self._finish(work, "failed", "repeated_index_cursor", response, now)
                    return
                status = self.state.record_page(stream, page, utc(now))
        except (ValueError, RecursionError) as exc:
            reason = str(exc) if type(exc) is ValueError else "invalid_index_json"
            self._finish(work, "failed", reason, response, now)
            return
        self._attempt(work, status, "index_response_recorded", response, now)

    def _probe_result(self, work, response, now):
        code = response.status
        if code in {404, 410}:
            self._finish(work, "missing", f"http_{code}", response, now)
            return
        if work.method == "HEAD" and code in {200, 405, 501}:
            self._finish(work, "deferred", "head_requires_get", response, now, method="GET")
        elif code == 200:
            provider = work.variant["board_key"].split(":", 1)[0]
            try:
                data = json.loads(response.body)
                if provider == "greenhouse":
                    valid = isinstance(data, dict) and isinstance(data.get("name"), str) and bool(data["name"].strip())
                elif provider == "ashby":
                    valid = isinstance(data, dict) and isinstance(data.get("jobs"), list) and all(
                        isinstance(row, dict) and isinstance(row.get("jobUrl"), str) for row in data["jobs"])
                else:
                    valid = isinstance(data, list) and all(
                        isinstance(row, dict) and isinstance(row.get("id"), str) for row in data)
            except (ValueError, RecursionError):
                valid = False
            self._finish(work, "validated" if valid else "failed",
                         "get_board_schema" if valid else "invalid_board_response", response, now)
        else:
            self._finish(work, "failed", f"http_{code}", response, now)

    def _receive(self, work, response):
        self.bytes += response.bytes_read
        now = self.clock()
        stop = access_stop(response)
        if stop:
            self.robots_unavailable.add(work.origin)
        elif response.status in {429, 503}:
            delay = retry_seconds(response.headers.get("retry-after", ""), now, self.limits.retry_delay)
            self.cooldown[work.origin] = max(now + delay, self.cooldown.get(work.origin, 0))
        with self.state.store.transaction():
            if stop:
                self.state.set_origin(work.origin, now, stop)
                self._finish(work, "denied", stop, response, now)
                self._mark_index_unavailable(work.origin, "denied", stop)
            elif response.status in {429, 503}:
                self.state.set_origin(work.origin, self.cooldown[work.origin], "")
                reason = f"http_{response.status}"
                self._finish(work, "deferred", reason, response, now)
                if work.robots:
                    self.robots_unavailable.add(work.origin)
                    self._mark_index_unavailable(work.origin, "deferred", reason)
            elif work.robots:
                self._robots_result(work, response, now)
                if work.origin in self.robots_unavailable:
                    self._mark_index_unavailable(work.origin, "deferred", "robots_unverified")
            elif response.error:
                self._finish(work, "failed", response.error, response, now)
            elif work.stream is not None:
                if response.status == 200:
                    self._page_result(work, response, now)
                else:
                    self._finish(work, "failed", f"http_{response.status}", response, now)
            else:
                self._probe_result(work, response, now)

    def _mark_index_unavailable(self, origin, status, reason):
        for identifier in self.streams[origin]:
            stream = self.state.stream(identifier)
            if stream["status"] in RETRYABLE:
                self.state.set_stream(stream, status, reason)
                self.suspended.add(identifier)

    def _rotate_stream(self, work):
        if work.stream is not None:
            lanes = self.streams[work.origin]
            identifier = work.stream["id"]
            lanes.remove(identifier)
            lanes.append(identifier)

    def _schedule(self, lock_fd, deadline):
        """Return the next wake-up delay and whether a byte reservation blocked work."""
        delay, byte_blocked = None, False
        active = {origin: [] for origin in ORIGINS}
        for flight in self.inflight.values():
            active[flight.work.origin].append(flight)
        for _ in range(len(self.origins)):
            if self.stopping() or self.monotonic() >= deadline:
                break
            if len(self.inflight) + len(self.abandoned) >= self.limits.concurrency or self.requests >= self.limits.requests:
                break
            origin = self.origins.popleft()
            self.origins.append(origin)
            running = active[origin]
            rules = self._rules(origin)
            slots = self.limits.origin_concurrency.get(origin, 1)
            if len(running) >= slots or origin in self.robots_unavailable or any(not flight.start_saved for flight in running):
                continue
            if running and rules is None:
                continue  # Wait for robots or drain in-flight calls before refreshing expired rules.
            not_before, stopped = self.state.origin(origin)
            if stopped:
                # A prior invocation's access stop applies to newly configured campaigns too.
                with self.state.store.transaction():
                    self._mark_index_unavailable(origin, "denied", stopped)
                self.robots_unavailable.add(origin)
                continue
            work = self._work(origin, [flight.work for flight in running], rules is None)
            if work is None:
                continue
            now = self.clock()
            gap = max(not_before - now, self.state.dispatch_after(origin) - now,
                      self.next_start.get(origin, 0) - self.monotonic())
            if gap > 0:
                delay = gap if delay is None else min(delay, gap)
                continue
            denial = self._policy(work, rules)
            if not denial and rules is None:
                work = Work("GET", f"https://{origin}/robots.txt", robots=True)
                denial = self._policy(work, None)
            if denial:
                self._rotate_stream(work)
                with self.state.store.transaction():
                    self._finish(work, "denied", denial, Response(), now)
                    if work.robots:
                        self.robots_unavailable.add(origin)
                        self._mark_index_unavailable(origin, "denied", denial)
                delay = 0
                continue
            cap = 0 if work.method == "HEAD" else min(self.limits.bytes, self.limits.response_bytes)
            if work.robots:
                cap = min(cap, 512_000)
            if cap > self.limits.bytes - self.bytes - self.reserved:
                byte_blocked = True
                continue
            pacing = self.limits.origin_delays.get(origin, DELAYS[origin])
            if rules is not None:
                pacing = max(pacing, rules.delay)
            attempt_until = min(deadline, self.monotonic() + self.limits.timeout)
            robots_expires = self.robots[origin][0] if not work.robots else math.inf
            # A crash before the worker's start is checkpointed must honor the latest possible
            # start plus the gap. This write can block, so the worker checks the same deadline.
            self.state.set_dispatch_after(origin, self.clock() + attempt_until - self.monotonic() + pacing)
            flight = Dispatch(work, cap, attempt_until, pacing, robots_expires)
            self._dispatch(flight, lock_fd)
            self.inflight[flight.result] = flight
            self._rotate_stream(work)
            self.reserved += cap
            self.requests += 1
            self.pages += int(work.stream is not None)
        return delay, byte_blocked

    def _dispatch(self, flight, lock_fd):
        # A blocked socket can outlive the campaign. Keep the store's flock until
        # the physical call returns, while allowing the coordinator to exit.
        lease = os.dup(lock_fd)

        def before_send():
            wall, started = self.clock(), self.monotonic()
            origin = flight.work.origin
            if (self.dispatch_closed or self.stopping() or started >= flight.deadline or wall >= flight.robots_expires
                    or origin in self.robots_unavailable or wall < self.cooldown.get(origin, 0)):
                return 0
            flight.started.set_result((wall, started))
            return flight.deadline - started

        def request():
            work = flight.work
            try:
                try:
                    remaining = flight.deadline - self.monotonic()
                    if remaining <= 0:
                        response = Response(error="dispatch_window_expired")
                    else:
                        response = self.transport.request(work.method, work.url, flight.cap, remaining,
                                                          before_send=before_send)
                    finished = self.monotonic()
                finally:
                    os.close(lease)
            except BaseException as exc:
                flight.result.set_exception(exc)
            else:
                flight.result.set_result((response, finished))

        try:
            Thread(target=request, daemon=True).start()
        except RuntimeError:
            os.close(lease)
            raise

    def _collect(self, *, interrupt=False):
        self.abandoned = {future for future in self.abandoned if not future.done()}
        for future, flight in tuple(self.inflight.items()):
            origin = flight.work.origin
            if flight.started.done() and not flight.start_saved:
                wall, started = flight.started.result()
                self.state.set_dispatch_after(origin, wall + flight.pacing)
                self.next_start[origin] = started + flight.pacing
                flight.start_saved = True
            if future.done():
                del self.inflight[future]
                self.reserved -= flight.cap
                response, finished = future.result()
                if finished >= flight.deadline:
                    response = Response(bytes_read=response.bytes_read, error="request_deadline")
            elif interrupt or self.monotonic() >= flight.deadline:
                del self.inflight[future]
                self.abandoned.add(future)
                # The actual bytes are unknown. Charge the reservation in full.
                self.reserved -= flight.cap
                response = Response(bytes_read=flight.cap, error="interrupted" if interrupt else "request_deadline")
            else:
                continue
            if not 0 <= response.bytes_read <= flight.cap or len(response.body) > response.bytes_read:
                raise ValueError("Transport exceeded its response byte reservation")
            if response.error == "request_deadline":
                self.robots_unavailable.add(origin)
            self._receive(flight.work, response)

    def _run(self, lock_fd):
        deadline = self.monotonic() + self.limits.seconds
        idle_until = None
        while True:
            if self.stopping():
                return "interrupted"
            self._collect()
            delay, byte_blocked = self._schedule(lock_fd, deadline)
            now = self.monotonic()
            if self.inflight:
                remaining = min(flight.deadline for flight in self.inflight.values()) - now
                timeout = min(1, remaining) if delay is None else min(1, remaining, max(.01, delay))
                signals = set(self.inflight)
                signals.update(flight.started for flight in self.inflight.values() if not flight.start_saved)
                wait(signals, timeout=max(0, timeout), return_when=FIRST_COMPLETED)
                idle_until = None
                continue
            if now >= deadline:
                return "time_budget"
            if self.requests >= self.limits.requests:
                return "request_budget"
            if self.abandoned:
                return "request_deadline"
            if delay == 0:
                continue
            if delay is not None:
                idle_until = now + self.limits.idle_seconds if idle_until is None else idle_until
                if now + delay <= idle_until:
                    self.sleep(min(delay, deadline - now, 1))
                    continue
            if byte_blocked:
                return "byte_budget"
            if delay is not None:
                return "origin_cooldown"
            return "page_budget" if self.pages >= self.limits.pages else "queue_drained"

    def run(self) -> dict:
        with self.state.exclusive() as lock_fd:
            with self.state.store.transaction():
                self.denied_routes = self.state.access_policy(
                    self.denied_routes, replace=self.replace_access_policy
                )
            if self.campaign == CATALOG_CAMPAIGN:
                self.state.require_catalog_campaign()
            else:
                self.state.setup(self.campaign, self.plan)
            self.attempt_checkpoint = self.state.last_attempt()
            for cached in self.state.robots():
                rules = RobotsRules("", USER_AGENT)
                rules.rules, rules.delay = json.loads(cached["rules"]), cached["delay"]
                self.robots[cached["origin"]] = (cached["expires"], rules)
            for stream in self.state.streams(self.campaign):
                self.streams[urlsplit(INDEX_URLS[stream["source"]]).hostname].append(stream["id"])
            try:
                try:
                    reason = self._run(lock_fd)
                except KeyboardInterrupt:
                    reason = "interrupted"
            finally:
                self.dispatch_closed = True
            while self.inflight:
                try:
                    self._collect(interrupt=True)
                except KeyboardInterrupt:
                    reason = "interrupted"
            return {"campaign": self.campaign, "invocation": self.invocation, "stop_reason": reason,
                    "requests": self.requests, "page_requests": self.pages, "bytes_read": self.bytes,
                    **self.state.summary(self.campaign)}
