"""Bounded enrichment of public search results from structured JobPosting metadata.

This is a conservative fallback, not a general-purpose web crawler. It never
requires login, bypasses access controls or treats a web result as a verified
open job. Failed extractions are retained with their status.
"""
from html.parser import HTMLParser
import ipaddress
import json
import re
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests

USER_AGENT = "CareerDiscoveryResearchBot/0.1"
MAX_HTML_CHARS = 600_000


def safe_public_url(url):
    """Allow only ordinary HTTPS web hostnames; exclude local/credential URLs."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        if parts.scheme != "https" or not host or parts.username or parts.password:
            return False
        if parts.port not in (None, 443):
            return False
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".test")):
            return False
        try:
            ipaddress.ip_address(host)
            return False
        except ValueError:
            return "." in host and len(host) <= 253
    except ValueError:
        return False


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_ld = False
        self.current = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "script":
            props = dict(attrs)
            self.in_ld = (props.get("type") or "").lower().split(";")[0].strip() == "application/ld+json"
            self.current = []

    def handle_data(self, data):
        if self.in_ld:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self.in_ld:
            self.scripts.append("".join(self.current))
            self.in_ld = False


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []
        self.skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = False

    def handle_data(self, data):
        if not self.skip:
            self.items.append(data)


def strip_html(value):
    if not isinstance(value, str):
        return ""
    parser = _Text()
    try:
        parser.feed(value)
        return re.sub(r"\s+", " ", " ".join(parser.items)).strip()
    except ValueError:
        return re.sub(r"\s+", " ", re.sub("<[^>]*>", " ", value)).strip()


def _all_nodes(value):
    if isinstance(value, list):
        for v in value:
            yield from _all_nodes(v)
    elif isinstance(value, dict):
        yield value
        if "@graph" in value:
            yield from _all_nodes(value["@graph"])


def parse_jobposting(html, expected_title=""):
    """Extract a single matching JobPosting. None if metadata absent/ambiguous."""
    from difflib import SequenceMatcher
    parser = _Scripts()
    try:
        parser.feed(html[:MAX_HTML_CHARS])
    except ValueError:
        return None
    jobs = []
    for script in parser.scripts:
        try:
            obj = json.loads(script.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        for candidate in _all_nodes(obj):
            kinds = candidate.get("@type", [])
            if isinstance(kinds, str):
                kinds = [kinds]
            if any(str(kind).split("/")[-1].lower() == "jobposting" for kind in kinds):
                jobs.append(candidate)
    if not jobs:
        return None
    if len(jobs) > 1:
        if not expected_title:
            return None
        normalize = lambda x: re.sub(r"[^a-z0-9]+", " ", str(x or "").lower()).strip()
        expected = normalize(expected_title)
        jobs.sort(key=lambda j: SequenceMatcher(None, expected, normalize(j.get("title", ""))).ratio(), reverse=True)
        if SequenceMatcher(None, expected, normalize(jobs[0].get("title", ""))).ratio() < 0.50:
            return None

    obj = jobs[0]
    org = obj.get("hiringOrganization")
    company = org.get("name", "") if isinstance(org, dict) else (org if isinstance(org, str) else "")
    locs = obj.get("jobLocation", [])
    if isinstance(locs, dict):
        locs = [locs]
    formatted = []
    for loc in locs if isinstance(locs, list) else []:
        if not isinstance(loc, dict):
            continue
        address = loc.get("address", {})
        if isinstance(address, dict):
            formatted.append(", ".join(str(address.get(k)) for k in ("addressLocality", "addressRegion") if address.get(k)))
        elif isinstance(address, str):
            formatted.append(address)
    location = "; ".join(dict.fromkeys(x for x in formatted if x))
    if not location and str(obj.get("jobLocationType", "")).upper() == "TELECOMMUTE":
        location = "Remote — eligibility requires verification"
    return {
        "title": strip_html(obj.get("title", ""))[:300],
        "company": strip_html(company)[:200],
        "location": location[:200],
        "description": strip_html(obj.get("description", ""))[:10_000],
        "posted_at": str(obj.get("datePosted", ""))[:50],
    }


def robots_allowed(session, url, cache, observations=None):
    """Respect robots rules; conservatively skip on inability to read them."""
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    evidence={"url":url,"robots_url":origin+"/robots.txt","cached":origin in cache}
    if origin not in cache:
        try:
            response = session.get(origin + "/robots.txt", headers={"User-Agent": USER_AGENT},
                                   timeout=12, allow_redirects=False)
            evidence["http_status"]=response.status_code
            if response.status_code == 404 or response.status_code == 410:
                cache[origin] = None
                evidence["reason"]="robots_not_published"
            elif response.status_code == 200:
                text=response.text.lstrip("\ufeff")
                if re.search(r'<(?:html|!doctype html)\b',text[:1000],re.I):
                    cache[origin]=False;evidence["reason"]="robots_invalid_or_challenge"
                else:
                    parser = RobotFileParser()
                    parser.parse(text[:100_000].splitlines())
                    cache[origin] = parser;evidence["reason"]="robots_rules_observed"
            else:
                cache[origin] = False
                evidence["reason"]="robots_http_"+str(response.status_code)
            if hasattr(response,"close"):response.close()
        except requests.RequestException as exc:
            cache[origin] = False
            evidence["reason"]="robots_network_"+type(exc).__name__
    rule = cache[origin]
    allowed=rule is None or (rule is not False and rule.can_fetch(USER_AGENT, url))
    if allowed and rule not in (None,False) and (rule.crawl_delay(USER_AGENT) or 0)>1:
        allowed=False;evidence["reason"]="stricter_crawl_delay_skip"
    evidence["allowed"]=allowed
    if "reason" not in evidence:evidence["reason"]="cached_rules_allow" if allowed else "cached_rules_deny_or_unavailable"
    if not allowed and evidence["reason"]=="robots_rules_observed":evidence["reason"]="robots_explicit_disallow"
    if observations is not None:observations.append(evidence)
    return allowed


def enrich_public_page(session, url, title="", robots_cache=None):
    """Return (fields, status). Follow at most two validated HTTPS redirects."""
    cache = robots_cache if robots_cache is not None else {}
    current = url
    for _ in range(3):
        if not safe_public_url(current):
            return {}, "unsafe_url"
        if not robots_allowed(session, current, cache):
            return {}, "robots_disallowed_or_unavailable"
        try:
            response = session.get(current, headers={"User-Agent": USER_AGENT},
                                   timeout=12, allow_redirects=False)
        except requests.RequestException:
            return {}, "network_error"
        if 300 <= response.status_code < 400:
            redirect = response.headers.get("Location", "")
            if not redirect:
                return {}, "redirect_without_target"
            current = urljoin(current, redirect)
            continue
        if response.status_code != 200:
            return {}, f"http_{response.status_code}"
        mime = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if mime not in ("text/html", "application/xhtml+xml", ""):
            return {}, "not_html"
        job = parse_jobposting(response.text[:MAX_HTML_CHARS], title)
        if job is None:
            return {}, "no_jobposting_metadata"
        return job, "jobposting_metadata"
    return {}, "too_many_redirects"

