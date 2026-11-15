"""One bounded request at a time. The discovery scheduler owns retries and pacing."""
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timezone
from email.utils import parsedate_to_datetime
import math
from threading import Lock
import time
from typing import Callable, Mapping, Protocol
from urllib.parse import urlsplit

import requests
from urllib3.exceptions import HTTPError

from career_engine.board_indexes import INDEX_URLS, PROBE_TEMPLATES

ORIGINS = tuple(urlsplit(url).hostname for url in (*INDEX_URLS.values(), *PROBE_TEMPLATES.values()))
USER_AGENT = "ProspectBoardDiscovery"
CHALLENGE_PREFIX_BYTES = 8192


@dataclass(frozen=True)
class Response:
    status: int = 0
    headers: Mapping[str, str] = field(default_factory=dict)  # lowercase names
    body: bytes = b""
    bytes_read: int = 0
    error: str = ""


class Transport(Protocol):
    def request(self, method: str, url: str, byte_limit: int, seconds: float, *,
                before_send: Callable[[], float]) -> Response:
        """Call before_send at dispatch for the remaining time. Zero forbids sending."""
        ...


class RequestsTransport:
    def __init__(self, contact: str):
        if not contact or any(not 32 < ord(c) < 127 for c in contact):
            raise ValueError("Provide a public operator contact without spaces")
        self.sessions = {origin: requests.Session() for origin in ORIGINS}
        self._lock = Lock()
        self._active = 0
        self._closed = False
        for session in self.sessions.values():
            session.trust_env = False  # no proxy configuration or netrc authentication
            session.headers.update({"User-Agent": f"{USER_AGENT}/1.0 (contact: {contact})",
                                    "Accept-Encoding": "identity"})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        with self._lock:
            self._closed = True
            if not self._active:
                self._close_sessions()

    def _close_sessions(self):
        for session in self.sessions.values():
            session.close()

    @contextmanager
    def _session(self, origin):
        with self._lock:
            session = None if self._closed else self.sessions[origin]
            self._active += int(session is not None)
        try:
            yield session
        finally:
            if session is not None:
                with self._lock:
                    self._active -= 1
                    if self._closed and not self._active:
                        self._close_sessions()

    def request(self, method: str, url: str, byte_limit: int, seconds: float, *,
                before_send: Callable[[], float] | None = None) -> Response:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.netloc not in self.sessions or
                method not in ("HEAD", "GET")):
            raise ValueError("Unexpected board discovery request")
        deadline = time.monotonic() + seconds
        with self._session(parsed.netloc) as session:
            if session is None:
                return Response(error="request_deadline")
            return self._send(session, method, url, byte_limit, deadline, before_send)

    def _send(self, session, method, url, byte_limit, deadline, before_send):
        status, headers, count = 0, {}, 0
        try:
            # Sending through the adapter skips Session's redirect preparation,
            # which can otherwise consume an entire redirect response body.
            prepared = session.prepare_request(requests.Request(method, url))
            adapter = session.get_adapter(url)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return Response(error="request_deadline")
            if before_send is not None:
                remaining = min(remaining, before_send())
                if remaining <= 0:
                    return Response(error="dispatch_window_expired")
            with adapter.send(prepared, stream=True, timeout=remaining) as reply:
                if time.monotonic() >= deadline:
                    return Response(error="request_deadline")
                status = reply.status_code
                headers = {key.lower(): value for key, value in reply.headers.items()}
                if method == "HEAD":
                    reply.raw.read(0, decode_content=False)  # Release the zero-body response for keep-alive.
                    return (Response(status, headers) if time.monotonic() < deadline
                            else Response(error="request_deadline"))
                if 300 <= status < 400 or access_stop(Response(status, headers)):
                    return Response(status, headers)
                if headers.get("content-encoding", "identity").lower() != "identity":
                    return Response(status, headers, error="unexpected_content_encoding")
                if status != 200:
                    # Inspect a small prefix for body-only publisher challenges.
                    byte_limit = min(byte_limit, CHALLENGE_PREFIX_BYTES)
                chunks = bytearray()
                # read1 lets us check elapsed time after each buffered/socket read.
                read = reply.raw.read1 if hasattr(type(reply.raw), "read1") else reply.raw.read
                while count < byte_limit and time.monotonic() < deadline:
                    part = read(min(65_536, byte_limit - count), decode_content=False)
                    count += len(part)
                    if time.monotonic() >= deadline:
                        return Response(bytes_read=count, error="request_deadline")
                    if not part:
                        return Response(status, headers, bytes(chunks), count)
                    chunks.extend(part)
                    if count - len(part) < CHALLENGE_PREFIX_BYTES:
                        prefix = bytes(chunks[:CHALLENGE_PREFIX_BYTES])
                        if access_stop(Response(status, headers, prefix)):
                            return Response(status, headers, prefix, count)
                if time.monotonic() >= deadline:
                    return Response(bytes_read=count, error="request_deadline")
                if status != 200 or reply.raw.length_remaining == 0:
                    return Response(status, headers, bytes(chunks), count)
                return Response(status, headers, bytes(chunks[:CHALLENGE_PREFIX_BYTES]), count, "response_byte_limit")
        except (requests.RequestException, HTTPError, OSError):
            if time.monotonic() >= deadline:
                return Response(bytes_read=count, error="request_deadline")
            return Response(status, headers, bytes_read=count, error="network_failure")


def retry_seconds(value: str, now: float, fallback: float) -> float:
    try:
        if value.isascii() and value.isdecimal():
            delay = float(value)
        else:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            delay = date.timestamp() - now
    except (AttributeError, TypeError, ValueError, OverflowError, OSError):
        return fallback
    return max(delay, fallback) if math.isfinite(delay) else fallback


def access_stop(response: Response) -> str:
    if response.status in (401, 403, 451):
        return f"http_{response.status}"
    prefix = response.body[:CHALLENGE_PREFIX_BYTES].removeprefix(b"\xef\xbb\xbf").lower()
    location = response.headers.get("location", "").lower()
    if (response.headers.get("cf-mitigated", "").lower() == "challenge" or
            (300 <= response.status < 400 and any(marker in location for marker in (
                "/login", "/sign-in", "/signin", "/captcha", "/challenge"))) or
            (prefix.lstrip().startswith(b"<") and any(marker in prefix for marker in (
                b"/cdn-cgi/challenge-platform/", b"captcha", b"<title>just a moment")))):
        return "challenge"
    return ""
