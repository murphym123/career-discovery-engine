"""Public ATS index requests and evidence parsing for opt-in board discovery.

Domain-wide CDX, urlscan supplementation and slug filtering adapted from
mherzog4/job-boards (Copyright 2026 Matt Herzog, MIT). See
LICENSES/job-boards-MIT.txt for the upstream notice.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re
from urllib.parse import unquote_plus, urlencode, urlsplit, urlunsplit

from career_engine.ats import PUBLIC_HOSTS, board_from_url

INDEX_URLS = {
    "wayback": "https://web.archive.org/cdx/search/cdx",
    "urlscan": "https://urlscan.io/api/v1/search/",
    "commoncrawl": "https://index.commoncrawl.org/",
}
EVIDENCE_KINDS = {
    "wayback": "internet_archive_url_index",
    "urlscan": "urlscan_public_search",
    "commoncrawl": "commoncrawl_url_index",
}
PROBE_TEMPLATES = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{}",
    "lever": "https://api.lever.co/v0/postings/{}?mode=json&limit=1",
    "lever_eu": "https://api.eu.lever.co/v0/postings/{}?mode=json&limit=1",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{}",
}
JUNK = {"_next", "api", "static", "assets", "meeting", "b", "favicon.ico",
        "robots.txt", "sitemap.xml", "embed", "css", "js", "images", "img"}
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)
COLLECTION = re.compile(r"CC-MAIN-\d{4}-\d{2}")


@dataclass(frozen=True)
class Plan:
    through: datetime
    archive_from: str = ""
    recent_days: int = 30
    page_size: int = 2_000
    urlscan_size: int = 100
    commoncrawl: str = ""

    def __post_init__(self):
        if self.through.utcoffset() is None:
            raise ValueError("through needs a timezone")
        if self.archive_from:
            if not re.fullmatch(r"\d{8}", self.archive_from):
                raise ValueError("archive_from must be YYYYMMDD")
            if datetime.strptime(self.archive_from, "%Y%m%d").date() > self.through.astimezone(timezone.utc).date():
                raise ValueError("archive_from is after through")
        if self.recent_days < 0 or min(self.page_size, self.urlscan_size) < 1:
            raise ValueError("invalid index window or page size")
        if self.commoncrawl and not COLLECTION.fullmatch(self.commoncrawl):
            raise ValueError("invalid Common Crawl collection ID")


@dataclass(frozen=True)
class Page:
    rows: list[tuple[object, object, str]]  # observed URL, source timestamp, scan UUID (if any)
    cursor: str | None
    rejected: int = 0


def index_url(plan: Plan, source: str, host: str, cursor: str, total: int | None) -> str:
    endpoint = INDEX_URLS[source]
    through = plan.through.astimezone(timezone.utc)
    if source == "wayback":
        params = {"url": host, "matchType": "host", "fl": "original,timestamp",
                  "filter": "statuscode:200", "collapse": "urlkey", "output": "json",
                  "limit": plan.page_size, "showResumeKey": "true", "to": through.strftime("%Y%m%d%H%M%S")}
        if plan.archive_from:
            params["from"] = plan.archive_from
        if cursor:
            # CDX returns an already form-encoded key. Encode it exactly once.
            params["resumeKey"] = unquote_plus(cursor)
    elif source == "urlscan":
        start = through - timedelta(days=plan.recent_days)
        dates = f"[{start:%Y-%m-%dT%H:%M:%SZ} TO {through:%Y-%m-%dT%H:%M:%SZ}]"
        params = {"q": f"page.domain:{host} AND visibility:public AND date:{dates}",
                  "size": plan.urlscan_size}
        if cursor:
            params["search_after"] = cursor
    else:
        endpoint += plan.commoncrawl + "-index"
        params = {"url": host + "/*", "output": "json", "fl": "url,timestamp,status",
                  "filter": "status:200", "pageSize": 1}
        params.update({"showNumPages": "true"} if total is None else {"page": cursor})
    return endpoint + "?" + urlencode(params)


def board_probe_url(board: dict) -> str:
    return PROBE_TEMPLATES[board["provider"]].format(urlsplit(board["url"]).path[1:])


def candidate(url: object, host: str) -> tuple[dict, str] | None:
    """Use Prospect identities and retain only public URL components as evidence."""
    if not isinstance(url, str) or not url or len(url) > 16_384 or any(c.isspace() for c in url):
        return None
    try:
        parsed = urlsplit(url)
        if (parsed.hostname != host or host not in PUBLIC_HOSTS or parsed.netloc.lower() not in
                (host, host + ":443", host + ":80")):
            return None
        if parsed.port == 80 and parsed.scheme == "http":
            parsed = parsed._replace(netloc=host)  # historical HTTP captures often include :80
        if parsed.path.startswith("//"):
            return None
    except ValueError:
        return None
    board = board_from_url(parsed.geturl())
    if board is None:
        return None
    slug = board["token"].lower()
    if (slug in JUNK or re.fullmatch(r"[0-9a-f-]{30,}", slug) or
            (board["provider"] == "ashby" and slug.startswith("root."))):
        return None
    path = urlsplit(board["url"]).path
    suffix = parsed.path.strip("/").split("/")[1:]
    if board["provider"] == "greenhouse":
        if len(suffix) == 2 and suffix[0] == "jobs" and re.fullmatch(r"[0-9]{1,20}", suffix[1]):
            path += "/jobs/" + suffix[1]
    elif len(suffix) == 1 and UUID.fullmatch(suffix[0]):
        path += "/" + suffix[0]
    return board, urlunsplit((parsed.scheme, host, path, "", ""))


def _wayback(data: object) -> Page:
    if not isinstance(data, list):
        raise ValueError("wayback_schema")
    if not data:
        return Page([], None)
    header, *rows = data
    if not isinstance(header, list) or not all(field in header for field in ("original", "timestamp")):
        raise ValueError("wayback_header")
    original, timestamp = header.index("original"), header.index("timestamp")
    cursor = None
    # CDX JSON ends with an empty row and a single-cell continuation row.
    if len(rows) >= 2 and rows[-2] == []:
        tail = rows[-1]
        if (not isinstance(tail, list) or len(tail) != 1 or not isinstance(tail[0], str)
                or not 0 < len(tail[0]) <= 32_768 or any(c.isspace() for c in tail[0])):
            raise ValueError("wayback_resume_key")
        cursor = tail[0]
        rows = rows[:-2]
    if any(not isinstance(row, list) or len(row) != len(header) for row in rows):
        raise ValueError("wayback_row_shape")
    return Page([(row[original], row[timestamp], "") for row in rows], cursor)


def _urlscan(data: object) -> Page:
    if (not isinstance(data, dict) or not isinstance(data.get("results"), list)
            or type(data.get("has_more")) is not bool):
        raise ValueError("urlscan_schema")
    rows, rejected, cursor = [], 0, None
    results = data["results"]
    if data["has_more"]:
        last = results[-1] if results else None
        sort = last.get("sort") if isinstance(last, dict) else None
        if (not isinstance(sort, list) or not sort or any(
                type(part) not in (str, int) or not str(part) or "," in str(part) for part in sort)):
            raise ValueError("urlscan_search_after")
        cursor = ",".join(map(str, sort))
        if len(cursor) > 1_024:
            raise ValueError("urlscan_search_after")
    for item in results:
        task = item.get("task") if isinstance(item, dict) else None
        page = item.get("page") if isinstance(item, dict) else None
        if not isinstance(task, dict) or task.get("visibility") != "public" or not isinstance(page, dict):
            rejected += 1
            continue
        identifier = item.get("_id") or task.get("uuid")
        if not isinstance(identifier, str) or not UUID.fullmatch(identifier):
            rejected += 1
            continue
        rows.append((page.get("url"), task.get("time"), identifier))
    return Page(rows, cursor, rejected)


def parse_page(source: str, body: bytes, cursor: str, total: int | None) -> Page:
    if source == "wayback":
        return _wayback(json.loads(body))
    if source == "urlscan":
        return _urlscan(json.loads(body))
    rows = []
    for line in body.splitlines():
        if line.strip():
            item = json.loads(line)
            if not isinstance(item, dict) or "url" not in item:
                raise ValueError("commoncrawl_schema")
            rows.append((item["url"], item.get("timestamp"), ""))
    next_page = int(cursor) + 1
    return Page(rows, str(next_page) if next_page < total else None)


def source_time(value: object) -> str:
    if isinstance(value, str) and re.fullmatch(r"\d{14}|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z", value):
        return value
    return ""
