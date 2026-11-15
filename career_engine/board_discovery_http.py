"""One bounded request at a time. The discovery scheduler owns retries and pacing."""
from dataclasses import dataclass, field
from datetime import timezone
from email.utils import parsedate_to_datetime
import math
import time
from typing import Mapping, Protocol
from urllib.parse import urlsplit

import requests
from urllib3.exceptions import HTTPError

from career_engine.board_indexes import INDEX_URLS, PROBE_TEMPLATES

ORIGINS = tuple(urlsplit(url).hostname for url in (*INDEX_URLS.values(), *PROBE_TEMPLATES.values()))
USER_AGENT = "ProspectBoardDiscovery"


@dataclass(frozen=True)
class Response:
    status: int = 0
    headers: Mapping[str, str] = field(default_factory=dict)  # lowercase names
    body: bytes = b""
    bytes_read: int = 0
    error: str = ""


class Transport(Protocol):
    def request(self, method: str, url: str, byte_limit: int, seconds: float) -> Response:
        """Perform one anonymous request with no retries or redirects."""
        ...


class RequestsTransport:
    def __init__(self, contact: str):
        if not contact or any(not 32 < ord(c) < 127 for c in contact):
            raise ValueError("Provide a public operator contact without spaces")
        self.sessions = {origin: requests.Session() for origin in ORIGINS}
        for session in self.sessions.values():
            session.trust_env = False  # no proxy configuration or netrc authentication
            session.headers.update({"User-Agent": f"{USER_AGENT}/1.0 (contact: {contact})",
                                    "Accept-Encoding": "identity"})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        for session in self.sessions.values():
            session.close()

    def request(self, method: str, url: str, byte_limit: int, seconds: float) -> Response:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.netloc not in self.sessions or
                method not in ("HEAD", "GET")):
            raise ValueError("Unexpected board discovery request")
        session = self.sessions[parsed.netloc]
        status, headers, count = 0, {}, 0
        deadline = time.monotonic() + seconds
        try:
            # Sending through the adapter skips Session's redirect preparation,
            # which can otherwise consume an entire redirect response body.
            prepared = session.prepare_request(requests.Request(method, url))
            with session.get_adapter(url).send(prepared, stream=True, timeout=seconds) as reply:
                status = reply.status_code
                headers = {key.lower(): value for key, value in reply.headers.items()}
                if method == "HEAD":
                    reply.raw.read(0, decode_content=False)  # Release the zero-body response for keep-alive.
                    return Response(status, headers)
                if status != 200:
                    return Response(status, headers)
                if headers.get("content-encoding", "identity").lower() != "identity":
                    return Response(status, headers, error="unexpected_content_encoding")
                chunks = bytearray()
                # read1 lets us check elapsed time after each buffered/socket read.
                read = reply.raw.read1 if hasattr(type(reply.raw), "read1") else reply.raw.read
                while count < byte_limit and time.monotonic() < deadline:
                    part = read(min(65_536, byte_limit - count), decode_content=False)
                    if not part:
                        return Response(status, headers, bytes(chunks), count)
                    chunks.extend(part)
                    count += len(part)
                if reply.raw.length_remaining == 0 and time.monotonic() < deadline:
                    return Response(status, headers, bytes(chunks), count)
                error = "response_byte_limit" if count >= byte_limit else "request_deadline"
                return Response(status, headers, bytes(chunks[:8192]), count, error)
        except (requests.RequestException, HTTPError, OSError):
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
    prefix = response.body[:8192].lower()
    location = response.headers.get("location", "").lower()
    if (response.headers.get("cf-mitigated", "").lower() == "challenge" or
            (300 <= response.status < 400 and any(marker in location for marker in (
                "/login", "/sign-in", "/signin", "/captcha", "/challenge"))) or
            (prefix.lstrip().startswith(b"<") and any(marker in prefix for marker in (
                b"/cdn-cgi/challenge-platform/", b"captcha", b"<title>just a moment")))):
        return "challenge"
    return ""
