"""Single-attempt HTTPS streaming for the issue #39 route guard."""
from contextlib import contextmanager
from dataclasses import dataclass
import signal
import time
from urllib.parse import urlsplit

import requests
from urllib3.exceptions import HTTPError, ReadTimeoutError

from career_engine.ats import FetchError
from career_engine.extract import USER_AGENT


@dataclass(frozen=True)
class Reply:
    status: int = 200
    media: str = "application/json"
    chunks: tuple = ()
    read: object = None
    location: str = ""
    connect: float = 0
    retry_after: str = ""
    content_length: str = ""
    encoding: str = ""
    authenticate: bool = False
    challenge: bool = False


@contextmanager
def request_alarm(seconds, reason):
    """Bound DNS, slow headers and trickling reads as well as socket idle time."""
    if seconds <= 0:
        raise FetchError(reason)
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise RuntimeError("Pilot requires exclusive use of the request timer")

    def expired(*_):
        raise FetchError(reason)

    previous = signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class HTTPSWire:
    """Anonymous, fresh requests. Only the route guard supplies destinations."""

    def __init__(self):
        self.started = time.monotonic()
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))

    @property
    def time(self):
        return time.monotonic() - self.started

    @staticmethod
    def sleep(seconds):
        time.sleep(seconds)

    def close(self):
        self.session.close()

    @contextmanager
    def open(self, url, seconds, timeout_reason):
        if urlsplit(url).scheme != "https":
            raise FetchError("unsafe_endpoint")
        headers = {
            "User-Agent": USER_AGENT + " (+https://github.com/murphym123/career-discovery-engine)",
            "Accept": "application/json, application/x-ndjson, text/html, text/xml, text/plain",
            "Accept-Encoding": "identity",
            "Connection": "close",
        }
        request = requests.Request("GET", url, headers=headers).prepare()
        response = None
        with request_alarm(seconds, timeout_reason):
            try:
                # Session.send can read a redirect body to prepare Response.next.
                # Use its adapter directly so only the guard handles redirects and
                # no session cookies, environment proxies or netrc are consulted.
                response = self.session.get_adapter(url).send(
                    request, stream=True, timeout=(5, 10), verify=True, cert=None, proxies={},
                )
                yield Reply(
                    status=response.status_code,
                    read=lambda size: response.raw.read(size, decode_content=False),
                    media=response.headers.get("Content-Type", ""),
                    location=response.headers.get("Location", ""),
                    retry_after=response.headers.get("Retry-After", ""),
                    content_length=response.headers.get("Content-Length", ""),
                    encoding=response.headers.get("Content-Encoding", ""),
                    authenticate="WWW-Authenticate" in response.headers,
                    challenge=response.headers.get("CF-Mitigated", "").lower() == "challenge",
                )
            except (requests.exceptions.Timeout, ReadTimeoutError):
                raise FetchError("request_timeout") from None
            except (requests.RequestException, HTTPError, OSError):
                raise FetchError("network_error" if response is None else "stream_error") from None
            finally:
                if response is not None:
                    response.close()
