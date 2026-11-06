"""Conservative identity, geography and exploration helpers for broad retrieval."""
import hashlib
import re
from datetime import datetime, timezone


def geography(location):
    value = re.sub(r"[^a-z0-9]+", " ", str(location or "").lower()).strip()
    value = re.sub(r"\bwashington d c\b","washington dc",value)
    if not value:
        return "unknown"
    # Verified provider country/state/city order; bare Washington is ambiguous.
    if re.fullmatch(r'(?:us|usa|united states) dc(?: washington)?',value) or re.fullmatch(r'(?:us|usa|united states) district of columbia(?: washington)?',value) or re.fullmatch(r'dc washington (?:us|usa|united states)',value):
        return 'dc_dmv'
    if re.search(r"\b(washington dc|washington district of columbia|district of columbia)\b", value):
        return "dc_dmv"
    if re.search(r"\b(arlington|alexandria|bethesda|silver spring|rockville|fairfax|falls church|tysons|reston|mclean|college park|camp springs|forestville|upper marlboro|greenbelt|hyattsville|chevy chase|gaithersburg|herndon|dulles|ashburn|manassas|woodbridge)\b", value):
        # Require state or an unambiguous local city, not Arlington TX.
        if re.search(r"\b(va|virginia|md|maryland)\b", value) or value in ("bethesda", "silver spring"):
            return "dc_dmv"
    if re.search(r"\b(remote|worldwide|anywhere)\b", value):
        if re.search(r"\b(usa|us|united states|worldwide|anywhere)\b", value):
            return "us_remote"
        return "remote_eligibility_unknown"
    if value in ("us", "usa", "united states"):
        return "unknown"  # A country alone does not establish remote eligibility.
    if re.search(r"\b(maryland|virginia|dmv|northern virginia)\b",value) and not re.search(r"\b(richmond|charlottesville|lynchburg|norfolk|baltimore|annapolis|west virginia)\b",value):
        if re.fullmatch(r"(?:based in |location )?(?:maryland|virginia|dmv|northern virginia)",value) or "dmv" in value:
            return "regional_worksite_unknown"
    return "elsewhere"


def expired(job, now=None):
    text = job.get("closing_at", "")
    if not text:
        return False
    try:
        close = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if len(text) == 10:
            close = close.replace(hour=23, minute=59, second=59)
        if close.tzinfo is None:
            close = close.replace(tzinfo=timezone.utc)
        return close < (now or datetime.now(timezone.utc))
    except (ValueError, TypeError):
        return False  # Unknown/invalid dates never prove closure.


def screening_hold(job):
    """Route obvious senior/specialist titles to review, never erase records.

    These are conservative triage flags, not qualification determinations. Some
    'manager' or interdisciplinary roles may fit; reviewers can recover them.
    """
    title = re.sub(r"[^a-z0-9]+", " ", job.get("title", "").lower())
    if re.search(r"\b(senior|sr|principal|director|vice president|vp|chief|manager|lead|expert)\b", title):
        return "seniority_review"
    if re.search(r"\b(engineer|engineering|developer|architect|physician|registered nurse|scientist|attorney|counsel|accountant|financial analyst|audit|cloud security|site reliability)\b", title):
        return "specialist_background_review"
    return ""


def deduplicate(jobs):
    """Merge URL aliases or identical keys, retaining richer data and provenance.

    Distinct provider IDs are distinct requisitions even if titles match. We do
    not guess cross-board identity from titles when both provide distinct IDs.
    """
    groups, by_url, by_key = {}, {}, {}
    for job in jobs:
        matches = {key for key in (by_url.get(job["url"]), by_key.get(job["key"])) if key is not None}
        identity = min(matches) if matches else len(groups)
        while identity in groups and not matches:
            identity += 1
        records = [groups.pop(key) for key in sorted(matches)] + [job]
        # Structured records take precedence over an unverified search hit.
        records.sort(key=lambda x: (x["source"] != "brave", bool(x.get("source_id")),
                                    len(x.get("description", "")), x.get("score", 0)), reverse=True)
        merged = dict(records[0])
        merged["sources"] = ",".join(sorted({s for r in records for s in r.get("sources", r["source"]).split(",") if s}))
        for record in records[1:]:
            for field in ("description", "snippet", "company", "location", "posted_at", "closing_at"):
                if not merged.get(field) and record.get(field):
                    merged[field] = record[field]
        groups[identity] = merged
        for mapping in (by_url, by_key):
            for alias, key in list(mapping.items()):
                if key in matches:
                    mapping[alias] = identity
        by_url[job["url"]] = identity
        by_key[job["key"]] = identity
    return list(groups.values())


def exploration_sample(jobs, limit):
    """Deterministic sample independent of keyword score, stable for review."""
    return sorted(jobs, key=lambda j: hashlib.sha256(j["url"].encode()).hexdigest())[:limit]
