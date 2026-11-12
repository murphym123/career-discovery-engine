"""Bounded, free structured discovery APIs; no employer lists or paid fallback.

All network operations are GETs to fixed provider endpoints. Missing credentials
fail before a request. Page caps are per source, including Muse's location sweep.
"""
import json
import os
from dataclasses import dataclass, field

import requests
from career_engine.extract import strip_html

REQUIREMENTS = {
    "muse": ("MUSE_API_KEY",),
    "reliefweb": ("RELIEFWEB_APPNAME",),
    "usajobs": ("USAJOBS_API_KEY", "USAJOBS_USER_AGENT"),
}
DEFAULT_SOURCES = ("remotive", "jobicy")


@dataclass
class SourceStats:
    requests: int = 0
    received: int = 0
    malformed: int = 0
    pages: int = 0
    capped: bool = False
    status: str = "ok"
    unique: int = 0
    recommended: int = 0
    inspection_strategy: str = ""
    details_planned: int = 0
    details_attempted: int = 0
    descriptions_observed: int = 0
    index_only: int = 0
    errors: list = field(default_factory=list)
    index_attempts: list = field(default_factory=list)
    access_observations: list = field(default_factory=list)


def free_sources(environ=None):
    env = os.environ if environ is None else environ
    configured = [name for name, keys in REQUIREMENTS.items() if all(env.get(k) for k in keys)]
    return [*DEFAULT_SOURCES, *configured]


def missing_credentials(source):
    return [key for key in REQUIREMENTS.get(source, ()) if not os.getenv(key)]


def _names(items):
    if not isinstance(items, list):
        return ""
    return "; ".join(str(x.get("name", "")) for x in items if isinstance(x, dict) and x.get("name"))


def _job(source, item, **fields):
    return dict(source=source, source_id=str(item.get("id", "")), **fields)


def normalize(source, item):
    """Map provider records without filtering on titles, categories or seniority."""
    if not isinstance(item, dict):
        raise ValueError("record is not an object")
    if source == "jobicy":
        return _job(source, item, title=strip_html(item.get("jobTitle", "")),
                    company=item.get("companyName", ""),
                    location="Remote; " + str(item.get("jobGeo") or "eligibility unknown"),
                    url=item.get("url", ""), snippet=strip_html(item.get("jobExcerpt", "")),
                    description=strip_html(item.get("jobDescription", ""))[:10000],
                    posted_at=item.get("pubDate", ""))
    if source == "muse":
        return _job(source, item, title=item.get("name", ""),
                    company=(item.get("company") or {}).get("name", ""),
                    location=_names(item.get("locations")),
                    url=(item.get("refs") or {}).get("landing_page", ""),
                    description=strip_html(item.get("contents", ""))[:10000],
                    snippet=_names(item.get("categories")), posted_at=item.get("publication_date", ""))
    if source == "reliefweb":
        f = item.get("fields") or {}
        dates = f.get("date") or {}
        return _job(source, item, title=f.get("title", ""), company=_names(f.get("source")),
                    location="; ".join(x for x in (_names(f.get("city")), _names(f.get("country"))) if x),
                    url=f.get("url_alias") or f.get("url", ""),
                    description=strip_html(f.get("body-html") or f.get("body", ""))[:10000],
                    snippet=_names(f.get("career_categories")), posted_at=dates.get("created", ""),
                    closing_at=dates.get("closing", ""))
    if source == "usajobs":
        f = item.get("MatchedObjectDescriptor") or {}
        details = (f.get("UserArea") or {}).get("Details") or {}
        return dict(source=source, source_id=str(item.get("MatchedObjectId", "")),
                    title=f.get("PositionTitle", ""), company=f.get("OrganizationName", ""),
                    location=f.get("PositionLocationDisplay", ""), url=f.get("PositionURI", ""),
                    description=strip_html(" ".join(str(x or "") for x in (
                        details.get("JobSummary"), f.get("QualificationSummary"), details.get("Requirements"))))[:10000],
                    posted_at=f.get("PublicationStartDate", ""), closing_at=f.get("ApplicationCloseDate", ""))
    raise ValueError("unsupported structured source")


def retrieve(session, source, max_pages=3, stats=None, *, _evidence=None):
    """Yield usable records, preserving earlier pages when later pages fail.

    Never print exception URLs (Muse's free registration key is a query param).
    Rate limits stop the source immediately; no automatic retries or paid APIs.
    """
    stats = stats if stats is not None else SourceStats()
    if source not in ("jobicy", *REQUIREMENTS):
        raise ValueError("unsupported structured source")
    if not 1 <= max_pages <= 20:
        raise ValueError("page cap must be 1–20")
    if source == "usajobs" and _evidence is None and os.getenv("PROSPECT_DATA_DIR"):
        from career_engine.store.collectors import collect_usajobs, configured_run

        with configured_run(dict(collector="usajobs", max_pages=max_pages)) as run:
            yield from collect_usajobs(*run, session, max_pages, stats)
        return
    missing = missing_credentials(source)
    if missing:
        stats.status = "not_configured"
        stats.errors.append("Missing " + ", ".join(missing))
        return
    cursor, seen_cursors = None, set()
    # Separate geographic partitions avoid spending the entire budget in DC city.
    locations = ("Washington, DC", "Arlington, VA", "Alexandria, VA", "Bethesda, MD", "Silver Spring, MD", "Flexible / Remote")
    exhausted = set()
    pages_by_location = {location: 0 for location in locations}
    for page in range(max_pages):
        params, headers = {}, {"Accept": "application/json", "User-Agent": "CareerDiscoveryResearchBot/0.1"}
        if source == "jobicy":
            url = "https://jobicy.com/api/jobs"
            params = {"count": 100, "geo": "usa"}
            if cursor:
                params["cursor"] = cursor
            # No Authorization header: commercial direct-link mode is excluded.
        elif source == "muse":
            available = [loc for loc in locations if loc not in exhausted]
            if not available:
                return
            location = min(available, key=lambda loc: pages_by_location[loc])
            url = "https://www.themuse.com/api/public/jobs"
            params = {"page": pages_by_location[location], "location": location,
                      "api_key": os.environ["MUSE_API_KEY"]}
        elif source == "reliefweb":
            url = "https://api.reliefweb.int/v2/jobs"
            params = {"appname": os.environ["RELIEFWEB_APPNAME"], "preset": "latest", "profile": "full",
                      "limit": 100, "offset": page * 100,
                      "filter": json.dumps({"field": "country.name", "value": "United States of America"})}
        else:
            url = "https://data.usajobs.gov/api/search"
            headers.update({"Host": "data.usajobs.gov", "User-Agent": os.environ["USAJOBS_USER_AGENT"],
                            "Authorization-Key": os.environ["USAJOBS_API_KEY"]})
            params = {"LocationName": "Washington, DC", "Radius": 35, "ResultsPerPage": 100,
                      "Page": page + 1, "Fields": "Full", "SortField": "opendate", "SortDirection": "Desc"}
        try:
            stats.requests += 1
            response = session.get(url, params=params, headers=headers, timeout=25, allow_redirects=False)
            if response.status_code == 429:
                stats.status = "rate_limited"
                stats.errors.append("HTTP 429; source stopped, respect Retry-After before another run")
                return
            if response.status_code != 200:
                stats.status = "failed"
                stats.errors.append(f"HTTP {response.status_code}")
                return
            if _evidence is not None and not getattr(response, "prospect_captured", False):
                _evidence.usajobs_response(getattr(response, "content", None))
            data = response.json()
            if not isinstance(data, dict) or data.get("success") is False:
                raise ValueError("invalid response")
            if source == "usajobs":
                result = data.get("SearchResult")
                if not isinstance(result, dict):
                    raise ValueError("missing SearchResult")
                rows = result.get("SearchResultItems")
            else:
                rows = data.get({"jobicy": "jobs", "muse": "results", "reliefweb": "data"}[source])
            if not isinstance(rows, list):
                raise ValueError("missing record array")
            if _evidence is not None:
                _evidence.usajobs_page(result.get("SearchResultCountAll"), len(rows))
            stats.pages += 1
            for item in rows:
                stats.received += 1
                try:
                    record = normalize(source, item)
                except (ValueError, TypeError, AttributeError):
                    stats.malformed += 1
                    continue
                if _evidence is not None:
                    _evidence.usajobs_sighting(record)
                if not record.get("title") or not record.get("url"):
                    stats.malformed += 1
                    continue
                yield record
            if source == "jobicy":
                cursor = data.get("nextCursor")
                if not rows or not cursor:
                    return
                if not isinstance(cursor, str) or cursor in seen_cursors:
                    raise ValueError("repeated or invalid cursor")
                seen_cursors.add(cursor)
            elif source == "muse":
                pages_by_location[location] += 1
                if not rows or pages_by_location[location] >= int(data.get("page_count", 0)):
                    exhausted.add(location)
                if len(exhausted) == len(locations):
                    return
            elif source == "reliefweb":
                if (page + 1) * 100 >= int(data.get("totalCount", 0)) or not rows:
                    return
            elif (page + 1) * 100 >= int(result.get("SearchResultCountAll", 0)) or not rows:
                return
        except (requests.RequestException, ValueError, TypeError, AttributeError):
            stats.status = "failed"
            stats.errors.append("Network or response error; details omitted to protect credentials")
            return
    stats.capped = True
