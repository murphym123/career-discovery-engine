"""Read-only Shortlist view fed by an explicit, versioned ``shortlist.json``.

The file is produced by the assessment workflow, not by this dashboard. Every
field is untrusted: text passes Prospect's metadata redaction, links must be
ordinary public HTTPS URLs, and unknown or missing values stay unknown.
The view never reads the discovery database, collectors, or applicant dossiers.
Contract: dashboard/SHORTLIST.md.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from career_engine.store.privacy import REDACTED, Projection

SCHEMA_VERSION = 1
FEEDBACK_SCHEMA_VERSION = 1
MAX_JOBS = 200
MAX_LIST_ITEMS = 5
MAX_TEXT = 400
MAX_FILE_BYTES = 2_000_000
REPO_ROOT = Path(__file__).resolve().parents[1]

# Mirrors review_priority bands in docs/prospect-ai-assessment-v1.md (PR #67/#71).
PRIORITY_LABELS = {
    "high": "High priority", "medium": "Possible fit",
    "prioritize": "Prioritize", "consider": "Consider", "verify_first": "Verify first",
    "deprioritize": "Deprioritize", "unassessed": "Unassessed",
}
# Matches availability_status in tools/prepare_shortlist.py (PR #75).
AVAILABILITY_LABELS = {
    "verified_current": "Verified current", "not_yet_verified": "Not yet verified",
    "no_longer_open": "No longer open",
}
# Every field Prospect's immediate-review export captures per listing (the preferred
# variant's ``data``; FIELDS plus derived keys in career_engine/immediate_review.py).
LISTING_FIELDS = {
    "title": "Title", "company": "Company", "location": "Location", "geography": "Geography",
    "work_arrangement": "Work arrangement", "employment_type": "Employment type",
    "category": "Category", "posted_at": "Posted", "updated_at": "Updated",
    "closing_at": "Closing", "record_kind": "Record kind", "detail_status": "Detail status",
    "evidence_status": "Evidence status",
}
SOURCE_FIELDS = {
    "source": "Source snapshot", "provider": "Provider", "board_key": "Board",
    "source_id": "Source job ID", "identity": "Identity", "retrieved_at": "Retrieved",
    "description_truncated": "Description truncated", "description_sha256": "Description SHA-256",
    "description_text_sha256": "Description text SHA-256", "omitted_urls": "Omitted links",
}
LISTING_LINKS = {"url": "Listing link", "original_posting_url": "Original posting link"}
KNOWN_LISTING_KEYS = {*LISTING_FIELDS, *SOURCE_FIELDS, *LISTING_LINKS, "description"}
WITHHELD = "withheld"
DESCRIPTION_LIMIT = 50_000
MAX_EXTRA_FIELDS = 30
# Known job-board hosts, used only to name the source when no listing data is supplied.
KNOWN_HOSTS = {
    "greenhouse.io": "Greenhouse", "lever.co": "Lever", "ashbyhq.com": "Ashby",
    "applytojob.com": "JazzHR", "jazzhr.com": "JazzHR", "icims.com": "iCIMS",
    "usajobs.gov": "USAJOBS", "myworkdayjobs.com": "Workday", "smartrecruiters.com": "SmartRecruiters",
    "workable.com": "Workable", "bamboohr.com": "BambooHR",
}
FEEDBACK_LABELS = {"would_apply": "Would apply", "maybe": "Maybe", "no": "No"}
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})"
)


class ShortlistError(ValueError):
    """The shortlist file exists but cannot be shown safely."""


def shortlist_path(environ=os.environ) -> Path | None:
    explicit = environ.get("PROSPECT_SHORTLIST", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    data_dir = environ.get("PROSPECT_DATA_DIR", "").strip()
    return Path(data_dir).expanduser() / "shortlist.json" if data_dir else None


def _text(privacy, value, limit=MAX_TEXT):
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    if not value or privacy.text(value) == REDACTED:
        return None
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _texts(privacy, values):
    if not isinstance(values, list):
        return []
    return [text for value in values[:MAX_LIST_ITEMS] if (text := _text(privacy, value))]


def _checked(value):
    """Return a display date for a real check date, or None."""
    if not isinstance(value, str):
        return None
    try:
        if _DATE.fullmatch(value):
            checked = date.fromisoformat(value)
            return f"{checked:%b} {checked.day}, {checked.year}"
        if _TIMESTAMP.fullmatch(value):
            checked = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
            return f"{checked:%b} {checked.day}, {checked.year} at {checked:%H:%M} UTC"
    except ValueError:
        return None
    return None


def _listing_value(privacy, value, limit=200):
    """Return the captured value as text, WITHHELD when redaction hides it, None when absent."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        value = str(value)
    if isinstance(value, list):
        items = [_listing_value(privacy, item, limit) for item in value[:MAX_LIST_ITEMS * 4]]
        return ", ".join(item for item in items if item) or "none"
    if not isinstance(value, str) or not value.strip():
        return None
    return _text(privacy, value, limit) or WITHHELD


def _description(privacy, value):
    """Keep the full description as text, redacting only unsafe words, never whole paragraphs."""
    if not isinstance(value, str) or not value.strip():
        return None
    lines = []
    for line in value[:DESCRIPTION_LIMIT].replace("\r\n", "\n").split("\n"):
        words, previous, redact_next = [], "", False
        for word in line.split():
            # Pairs catch "Bearer x"; a redacted "key:" or "key=" also hides the next word.
            unsafe = (redact_next or privacy.unsafe(word)
                      or bool(previous and privacy.unsafe(f"{previous} {word}")))
            redact_next = unsafe and word.endswith((":", "="))
            words.append(REDACTED if unsafe else word)
            previous = "" if unsafe else word
        line = " ".join(words)
        if line and privacy.unsafe(line):
            line = REDACTED
        lines.append(line)
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return text or WITHHELD


def _listing(privacy, raw):
    """Project the optional ``listing`` object: everything Prospect captured, as captured."""
    if not isinstance(raw, dict):
        return None
    fields = {key: _listing_value(privacy, raw.get(key)) for key in (*LISTING_FIELDS, *SOURCE_FIELDS)}
    for key in LISTING_LINKS:
        value = raw.get(key)
        fields[key] = _url(privacy, value) or (WITHHELD if isinstance(value, str) and value else None)
    description = raw.get("description")
    fields["description"] = _description(privacy, description)
    fields["description_length"] = len(description) if isinstance(description, str) else None
    extras = {}
    for key in sorted(k for k in raw if isinstance(k, str) and k not in KNOWN_LISTING_KEYS):
        if len(extras) == MAX_EXTRA_FIELDS:
            break
        if privacy.native(key) is not None:  # keys are untrusted too
            extras[key] = _listing_value(privacy, raw[key])
    fields["extra"] = extras
    return fields


def link_source(url):
    """Name the job board from a posting link, for files without listing data."""
    host = (urlsplit(url).hostname or "") if url else ""
    for suffix, name in KNOWN_HOSTS.items():
        if host == suffix or host.endswith("." + suffix):
            return name, host
    return (None, host) if host else (None, None)


def _url(privacy, value):
    """Keep the exact posting URL only when Prospect's URL projection accepts it."""
    if not isinstance(value, str) or len(value) > 2048 or privacy.url(value) is None:
        return None
    parts = urlsplit(value)
    return parts._replace(fragment="").geturl().replace(" ", "%20")


def _job(privacy, raw, index):
    if not isinstance(raw, dict):
        raise ShortlistError(f"jobs[{index}] is not an object")
    review_id = raw.get("review_id")
    if not isinstance(review_id, str) or privacy.native(review_id) is None:
        raise ShortlistError(f"jobs[{index}] has a missing or unsafe review_id")
    priority = raw.get("review_priority")
    status = raw.get("availability_status")
    checked = _checked(raw.get("availability_checked_at"))
    if status not in AVAILABILITY_LABELS or (status == "verified_current" and checked is None):
        # A verification claim without a real check date is not shown as verified.
        status = "not_yet_verified"
    official = _url(privacy, raw.get("official_url"))
    return {
        "review_id": review_id,
        "title": _text(privacy, raw.get("title"), 200),
        "employer": _text(privacy, raw.get("employer"), 200),
        "location": _text(privacy, raw.get("location"), 200),
        "work_arrangement": _text(privacy, raw.get("work_arrangement"), 80),
        "compensation_text": _text(privacy, raw.get("compensation_text"), 200),
        "official_url": official,
        "application_url": _url(privacy, raw.get("application_url")) or official,
        "fit_reasons": _texts(privacy, raw.get("fit_reasons")),
        "gaps_to_verify": _texts(privacy, raw.get("gaps_to_verify")),
        "review_priority": priority if priority in PRIORITY_LABELS else "unassessed",
        "availability_status": status,
        "availability_checked": checked,
        "listing": _listing(privacy, raw.get("listing")),
    }


def load_shortlist(path: Path | None) -> dict | None:
    """Return a validated shortlist, None when no file exists, or raise ShortlistError."""
    if path is None or not path.is_file():
        return None
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ShortlistError("shortlist.json is larger than 2 MB")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ShortlistError("shortlist.json is not valid UTF-8 JSON") from error
    if not isinstance(document, dict):
        raise ShortlistError("shortlist.json must contain a JSON object")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ShortlistError(f"Unsupported schema_version (expected {SCHEMA_VERSION})")
    jobs = document.get("jobs")
    if not isinstance(jobs, list):
        raise ShortlistError("shortlist.json needs a jobs array")
    if len(jobs) > MAX_JOBS:
        raise ShortlistError(f"shortlist.json has more than {MAX_JOBS} jobs")
    privacy = Projection()
    records = [_job(privacy, raw, index) for index, raw in enumerate(jobs)]
    if len({job["review_id"] for job in records}) != len(records):
        raise ShortlistError("review_id values must be unique")
    return {
        "demo": document.get("demo") is True,
        "generated_at": _checked(document.get("generated_at")),
        "jobs": records,
    }


def filter_jobs(jobs, *, search="", availability=(), priority=()):
    """Keep file order; filter only on the supplied fields, never on a score."""
    terms = " ".join(search.split()).casefold().split()
    shown = []
    for job in jobs:
        if availability and job["availability_status"] not in availability:
            continue
        if priority and job["review_priority"] not in priority:
            continue
        haystack = " ".join(
            job[key] or "" for key in ("title", "employer", "location", "work_arrangement")
        ).casefold()
        if all(term in haystack for term in terms):
            shown.append(job)
    return shown


# Local feedback: a sidecar file beside the shortlist, never inside the repo.

def feedback_path(shortlist: Path | None, environ=os.environ) -> Path | None:
    """Return where feedback may be saved, or None when it must stay session-only."""
    explicit = environ.get("PROSPECT_SHORTLIST_FEEDBACK", "").strip()
    if explicit:
        target = Path(explicit).expanduser()
    elif shortlist is not None:
        target = shortlist.with_name("shortlist-feedback.json")
    else:
        return None
    resolved = target.resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        return None  # Keep private reactions out of Git.
    return target


def load_feedback(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    entries = document.get("entries") if isinstance(document, dict) else None
    if not isinstance(entries, dict):
        return {}
    return {
        key: value for key, value in entries.items()
        if isinstance(value, dict) and value.get("decision") in FEEDBACK_LABELS
    }


def save_feedback(path: Path, review_id: str, decision: str | None) -> dict:
    """Atomically record or clear one decision and return all entries."""
    if decision is not None and decision not in FEEDBACK_LABELS:
        raise ValueError("Unsupported feedback decision")
    entries = load_feedback(path)
    if decision is None:
        entries.pop(review_id, None)
    else:
        entries[review_id] = {
            "decision": decision,
            "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    payload = json.dumps(
        {"schema_version": FEEDBACK_SCHEMA_VERSION, "entries": entries}, indent=2, sort_keys=True,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".shortlist-feedback-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload + "\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return entries
