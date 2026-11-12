"""Shareable metadata projection for the v1 export. No network verification."""
import html
import os
import re
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from career_engine.extract import safe_public_url

REDACTED = "[redacted]"
SECRET_NAME = re.compile(
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|auth(?:orization)?|bearer|"
    r"token|password|passwd|secret|session(?:[_-]?id)?|subscription|unsubscribe|"
    r"signature|sig|credential|ticket|sid|sso|oauth|reset|invite|magic[_-]?link|jwt|"
    r"expires|key-pair-id|policy|saml(?:request|response)|hdnts|code|state|"
    r"login|log-in|sign-in|signin|sign-out|signout|logout|private|mail|webmail|verify|"
    r"x-amz(?:-[a-z]+)*|x-goog(?:-[a-z]+)*)", re.I,
)
AUTH_PATH = re.compile(
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|auth(?:orization)?|token|"
    r"password|secret|session(?:[_-]?id)?|subscription|unsubscribe|signature|"
    r"sso|oauth|reset|invite|magic[_-]?link|login|log-in|sign-in|signin|logout|"
    r"webmail|mail|private|verify)", re.I,
)
SENSITIVE_TEXT = re.compile(
    r"(?:[^\s<>@]+@[^\s<>@]+|https?://|mailto:|file:|"
    r"(?:^|[\s(])(?:~/|/[^\s/]+/|[A-Z]:[\\/])|"
    r"\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|token|secret|"
    r"password|session(?:[_ -]?id)?|authorization(?:[_ -]?key)?|subscription[_ -]?id|"
    r"signature|credential|cookie|unsubscribe)\s*[:=]|"
    r"^(?:to|from|cc|bcc|reply-to)\s*:|\bBearer\s+[A-Za-z0-9._~+/=-]+|"
    r"\b(?:gh[pousr]_[A-Za-z0-9]+|github_pat_[A-Za-z0-9_]+|"
    r"AKIA[A-Z0-9]{16}|sk-[A-Za-z0-9_-]{12,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+))",
    re.I,
)
DNS_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}")
PATH_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ ~-]{0,119}")
OPAQUE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
PUBLIC_JOB_QUERY = {"gh_jid"}


def _decoded(value):
    # Decode escaped content for checks, never use it as the serialized value.
    return html.unescape(unquote(unquote(value)))


class Projection:
    def __init__(self):
        self.secrets = tuple(value for key, value in os.environ.items() if len(value) >= 6 and (
            key == "USAJOBS_USER_AGENT"
            or key.endswith(("_API_KEY", "_TOKEN", "_PASSWORD", "_SECRET"))
        ))

    def unsafe(self, value):
        decoded = _decoded(value)
        return (
            any(ord(char) < 32 or ord(char) == 127 for char in decoded)
            or bool(SENSITIVE_TEXT.search(decoded))
            or any(secret in decoded for secret in self.secrets)
        )

    def text(self, value):
        return REDACTED if self.unsafe(value) else value

    def native(self, value):
        if value and OPAQUE_ID.fullmatch(value) and not self.unsafe(value):
            return value
        return None

    def tenant_is_shareable(self, value):
        return bool(value and PATH_PART.fullmatch(value) and not self.unsafe(value))

    def identifier(self, value):
        if self.native(value) is None:
            raise ValueError("Snapshot contains an unsafe stable record ID")
        return value

    def url(self, value):
        if not value or not safe_public_url(value) or any(ord(char) < 32 for char in value):
            return None
        parsed = urlsplit(value)
        host = parsed.hostname
        if (not DNS_HOST.fullmatch(host) or host.endswith(".invalid")
                or self.unsafe(host) or AUTH_PATH.fullmatch(host.split(".")[0])):
            return None
        for segment in filter(None, parsed.path.split("/")):
            decoded = _decoded(segment)
            if (not PATH_PART.fullmatch(decoded) or decoded in (".", "..")
                    or AUTH_PATH.fullmatch(decoded) or self.unsafe(decoded)
                    or re.match(r"(?:token|secret|signature|session|password|auth|subscription|api[_-]?key)[._-]", decoded, re.I)):
                return None
        try:
            query = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=128)
        except ValueError:
            return None
        if any(SECRET_NAME.fullmatch(_decoded(key)) for key, _ in query):
            return None  # A signed or authenticated URL is not evidence of a public equivalent.
        public_query = sorted((key, val) for key, val in query
                              if key in PUBLIC_JOB_QUERY and re.fullmatch(r"[0-9]{1,20}", val)
                              and not self.unsafe(val))
        return urlunsplit(("https", host, parsed.path or "/", urlencode(public_query), ""))

    @staticmethod
    def placeholder(posting_id):
        return f"https://redacted.invalid/postings/{posting_id}"

    def posting_urls(self, row):
        observed = self.url(row["url"])
        canonical = self.url(row["canonical_url"])
        if observed is None:
            # A separately normalized known-provider public URL may be usable.
            public = urlsplit(canonical or "")
            greenhouse_job = (
                public.hostname in ("job-boards.greenhouse.io", "boards.greenhouse.io")
                and re.fullmatch(r"/[^/]+/jobs/[0-9]+/?", public.path)
            )
            federal_job = (
                public.hostname in ("www.usajobs.gov", "usajobs.gov")
                and re.fullmatch(r"/job/[0-9]+/?", public.path, re.I)
            )
            if not (greenhouse_job or federal_job):
                placeholder = self.placeholder(row["posting_id"])
                return placeholder, placeholder
            observed = canonical
        return observed, canonical or observed
