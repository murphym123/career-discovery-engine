"""Read-only display projection for private full-evidence assessment records.

The fit classes are provisional judgments, not current-opening or eligibility
determinations. Source data stays in an external private JSON file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from career_engine.store.privacy import Projection
from dashboard.shortlist import _checked, _text, _url, shortlist_path

MAX_BYTES = 4_000_000
MAX_ROWS = 2000
FIT_LABELS = {
    "great_fit": "Great fit",
    "possible": "Possible fit / review",
    "poor_fit": "Poor fit (provisional)",
}
EMPLOYER_NAMES = {
    "brookings": "Brookings Institution",
    "dga": "Democratic Governors Association",
    "pentagrp": "Penta Group",
    "quincyinst": "Quincy Institute",
    "nasafcu": "NASA Federal Credit Union",
    "nextgenfed": "NextGen Federal Systems",
    "noblereach": "NobleReach",
    "stradaeducation": "Strada Education Foundation",
    "inflowfed": "Inflow Federal",
}


class ReviewResultsError(ValueError):
    """Invalid review-results document."""


def review_results_path(environ=os.environ):
    explicit = environ.get("PROSPECT_REVIEW_RESULTS", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    shortlist = shortlist_path(environ)
    return shortlist.with_name("deep_review.json") if shortlist else None


def _row(privacy, raw, index):
    if not isinstance(raw, dict):
        raise ReviewResultsError(f"records[{index}] must be an object")
    rid = raw.get("review_id")
    if not isinstance(rid, str) or privacy.native(rid) is None:
        raise ReviewResultsError(f"records[{index}] has an invalid review ID")
    category = raw.get("fit_category")
    if category not in FIT_LABELS:
        raise ReviewResultsError(f"records[{index}] has an invalid fit category")
    name = _text(privacy, raw.get("employer"), 160)
    name = EMPLOYER_NAMES.get(name, name)
    checked = _checked(raw.get("availability_checked_at"))
    status = raw.get("availability_status")
    return {
        "review_id": rid,
        "title": _text(privacy, raw.get("title"), 200) or "Title unavailable",
        "employer": name or "Employer unavailable",
        "location": _text(privacy, raw.get("location"), 160),
        "fit_category": category,
        "shortlisted": raw.get("shortlisted") is True,
        "needs_further_evidence": raw.get("needs_further_evidence") is True,
        "posting_evidence": _text(privacy, raw.get("posting_evidence"), 1200),
        "applicant_evidence": _text(privacy, raw.get("applicant_evidence"), 1200),
        "barriers_or_unknowns": _text(privacy, raw.get("barriers_or_unknowns"), 1200),
        "official_url": _url(privacy, raw.get("official_url")),
        "availability_status": "verified_current" if status == "verified_current" and checked else "unverified",
        "availability_checked_at": checked,
    }


def load_review_results(path):
    if path is None or not path.is_file():
        return None
    if path.stat().st_size > MAX_BYTES:
        raise ReviewResultsError("Review-results file is too large")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, ValueError) as error:
        raise ReviewResultsError("Review-results file is not valid JSON") from error
    if not isinstance(raw, dict) or not isinstance(raw.get("records"), list):
        raise ReviewResultsError("Review-results file must contain a records array")
    if len(raw["records"]) > MAX_ROWS:
        raise ReviewResultsError("Review-results file has too many records")
    privacy = Projection()
    rows = [_row(privacy, item, i) for i, item in enumerate(raw["records"])]
    if len({r["review_id"] for r in rows}) != len(rows):
        raise ReviewResultsError("Review-results file contains duplicate IDs")
    return {"generated_at": _checked(raw.get("generated_at")), "records": rows}


def filter_reviews(records, *, category="all", search="", employer="All employers"):
    words = search.strip().casefold().split()
    return [
        r for r in records
        if (category == "all" or category == r["fit_category"])
        and (employer == "All employers" or employer == r["employer"])
        and all(w in (r["title"] + " " + r["employer"] + " " +
                       (r["location"] or "")).casefold() for w in words)
    ]
