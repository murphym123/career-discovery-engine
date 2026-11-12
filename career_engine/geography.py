"""Evidence-based geography for Prospect store labels."""
import json
import re
import unicodedata
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

from career_engine.markets import STATE_NAMES

DEFAULT_VERSION = "gazetteer-dc/1"
_GAZETTEERS = {
    DEFAULT_VERSION: Path(__file__).resolve().parents[1] / "markets" / "dc" / "gazetteer.yaml",
}
LOCAL = {"dc", "dmv_md", "dmv_va"}
STATES = {name.lower(): code for code, name in STATE_NAMES.items()}
STATES.update({code.lower(): code for code in STATE_NAMES})
STATE_SUFFIXES = sorted(STATES, key=lambda state: (-len(state), state))
PART_SEPARATOR = re.compile(r"[;|\n]+|\s+(?:/|or|and)\s+", re.I)
LOCAL_PREFIX = re.compile(
    r"^([^,]+),\s*(D\.?\s*C\.?|District of Columbia|MD|Maryland|VA|Virginia)(?=$|[ ,])",
    re.I,
)
US_COUNTRIES = {"us", "usa", "united states", "united states of america"}
COUNTRY_CODES = {
    "au": "australia", "aus": "australia", "ca": "canada", "can": "canada",
    "de": "germany", "deu": "germany", "fr": "france", "fra": "france",
    "gb": "united kingdom", "gbr": "united kingdom", "uk": "united kingdom",
    "ie": "ireland", "irl": "ireland", "in": "india", "ind": "india",
    "mx": "mexico", "mex": "mexico",
}
CONFLICT_REASON = "The worksite evidence contains conflicting locations or jurisdictions."


@dataclass(frozen=True)
class _Worksite:
    locality: str = ""
    state: str | None = None
    country: str | None = None
    conflict: bool = False


@lru_cache(maxsize=None)
def load_gazetteer(version=DEFAULT_VERSION):
    """Load a bundled, versioned gazetteer written in YAML's JSON subset."""
    path = _GAZETTEERS.get(version)
    if path is None:
        raise ValueError(f"Unknown geography classifier version: {version}")
    gazetteer = json.loads(path.read_text(encoding="utf-8"))
    if gazetteer["version"] != version:
        raise ValueError("Gazetteer file does not match the requested classifier version")
    return gazetteer


def _normalize(value):
    text = unicodedata.normalize("NFKD", value if isinstance(value, str) else "")
    text = "".join(char for char in text if not unicodedata.combining(char)).lower()
    text = re.sub(r"\bd\.\s*c\.?", "dc", text)
    text = re.sub(r"\bu\.\s*s\.?", "us", text)
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    return re.sub(r"\bd c\b", "dc", text)


def _parts(value):
    return [part.strip() for part in PART_SEPARATOR.split(value) if part.strip()] if isinstance(value, str) else []


def _state_location(text, original):
    # iCIMS and other providers supply a country/state/locality path.
    coded = re.fullmatch(r"(?:us|usa|united states) ([a-z]{2})(?: (.+))?", text)
    if coded and coded[1] in STATES:
        return coded[2] or "", STATES[coded[1]]
    if text in STATES and text != "washington":
        if len(text) != 2 or original.strip().isupper() or text in ("dc", "md", "va"):
            return "", STATES[text]
    for suffix in STATE_SUFFIXES:
        if text.endswith(" " + suffix):
            locality = text[: -len(suffix)].strip()
            # A lowercase English word at the end is weak state evidence.
            if suffix in ("in", "or", "me", "hi", "ok") and not re.search(
                r"[, ]" + suffix.upper() + r"(?:\b|$)", original
            ):
                continue
            if suffix == "washington" and "," not in original and locality not in ("seattle", "state of"):
                continue
            return locality, STATES[suffix]
    return text, None


def _country(value, gazetteer):
    country = _normalize(value)
    if country in US_COUNTRIES:
        return "us"
    country = COUNTRY_CODES.get(country, country)
    return country if country in gazetteer["outside_countries"] else None


def _split_country(text, gazetteer):
    countries = sorted(US_COUNTRIES | set(gazetteer["outside_countries"]), key=len, reverse=True)
    for country in countries:
        if text == country:
            return "", _country(country, gazetteer)
        if text.endswith(" " + country):
            return text[: -len(country)].strip(), _country(country, gazetteer)
    return text, None


def _parse_worksite(original, gazetteer):
    text = _normalize(original)
    text = re.sub(r"^(?:based in|location) ", "", text)
    text, country = _split_country(text, gazetteer)
    # Existing board labels append a branch marker after the city and state.
    text = re.sub(r"\s+(md|va) f(?: .+)?$", r" \1", text)
    text = re.sub(r"\s+\d{5}(?: \d{4})?$", "", text)
    if text in gazetteer["dc_aliases"]:
        locality, state = text, "DC"
    elif text in ("washington state", "state of washington"):
        locality, state = "", "WA"
    else:
        # Provider labels sometimes repeat a state abbreviation and its full name.
        for code, name in (("md", "maryland"), ("va", "virginia")):
            if text.endswith(f" {code} {name}") or text.endswith(f" {code} {code}"):
                text = text.rsplit(" ", 1)[0]
        locality, state = _state_location(text, original)
    prefix = LOCAL_PREFIX.match(original) if isinstance(original, str) else None
    if prefix:
        prefix_locality, prefix_state = _normalize(prefix[1]), STATES[_normalize(prefix[2])]
        if state and state != prefix_state:
            return _Worksite(conflict=True)
        if prefix_state == "DC" or prefix_locality in gazetteer["nearby_places"][prefix_state]:
            locality, state = prefix_locality, prefix_state
    return _Worksite(locality, state, country or ("us" if state else None),
                     conflict=bool(state and country and country != "us"))


def _jurisdictions_conflict(first, second):
    return bool(first.conflict or second.conflict
                or (first.state and second.state and first.state != second.state)
                or (first.country and second.country and first.country != second.country))


def _structured_jurisdiction(context, gazetteer):
    state = STATES.get(_normalize(context.get("state")))
    country = _country(context.get("country"), gazetteer)
    return _Worksite(state=state, country=country or ("us" if state else None),
                     conflict=bool(state and country and country != "us"))


def _with_jurisdiction(worksite, jurisdiction):
    if _jurisdictions_conflict(worksite, jurisdiction):
        return replace(worksite, conflict=True)
    return replace(worksite, state=worksite.state or jurisdiction.state,
                   country=worksite.country or jurisdiction.country)


def _place(worksite, gazetteer):
    if worksite.conflict:
        return "unknown", "The worksite evidence contains conflicting jurisdictions."
    locality = worksite.locality
    if worksite.country and worksite.country != "us":
        return "elsewhere", "The worksite explicitly identifies a country outside the United States."
    state = worksite.state or gazetteer["bare_nearby_places"].get(locality)
    if state == "DC":
        return "dc", "The worksite identifies the District of Columbia."
    if state in ("MD", "VA"):
        if locality in gazetteer["nearby_places"][state]:
            return "dmv_" + state.lower(), f"The worksite matches a confirmed nearby {STATE_NAMES[state]} locality."
        if locality in gazetteer["outside_places"][state]:
            return "elsewhere", f"The named {STATE_NAMES[state]} locality is outside the existing nearby footprint."
        return "unknown", f"The {STATE_NAMES[state]} worksite is unresolved under the current nearby footprint."
    if state:
        return "elsewhere", f"The worksite explicitly identifies {STATE_NAMES[state]}."
    if locality in gazetteer["ambiguous_names"]:
        return "unknown", "The place name needs a state or equivalent worksite evidence."
    return "unknown", "The worksite lacks a confirmed locality or jurisdiction."


def _structured_worksites(context, gazetteer):
    offered = context.get("worksites") or ()
    if isinstance(offered, str):
        offered = _parts(offered)
    elif isinstance(offered, dict):
        offered = (offered,)
    for site in offered:
        fields = site if isinstance(site, dict) else {}
        raw = site.get("location") if isinstance(site, dict) else site
        worksite = _with_jurisdiction(_parse_worksite(raw, gazetteer), _structured_jurisdiction(fields, gazetteer))
        result = _place(worksite, gazetteer)
        if not worksite.conflict:
            result = _remote_result(_normalize(raw), fields, gazetteer) or result
        yield worksite, result


def _choose_worksite(places):
    local = [(value, reason) for value, reason in places if value in LOCAL]
    if local:
        preferred = next((item for item in local if item[0] == "dc"), local[0])
        if len(places) > 1:
            return preferred[0], preferred[1] + " At least one listed worksite is nearby."
        return preferred
    remote = next((item for item in places if item[0] == "remote_us_eligible"), None)
    if remote:
        return remote
    if places and all(value == "elsewhere" for value, _ in places):
        return places[0] if len(places) == 1 else ("elsewhere", "All listed worksites have explicit nonlocal evidence.")
    if len(places) == 1:
        return places[0]
    return "unknown", "At least one listed worksite remains unresolved." if places else "No worksite is stated."


def _remote_result(raw, context, gazetteer):
    rules = gazetteer["remote"]
    scope = _normalize(context.get("remote_scope"))
    arrangement = _normalize(context.get("work_arrangement"))
    if re.search(rules["negation"], raw):
        return None
    if not (arrangement in ("remote", "fully remote") or scope or
            any(re.search(pattern, raw) for pattern in rules["markers"])):
        return None
    jurisdiction = _structured_jurisdiction(context, gazetteer)
    if any(re.search(rules["dc_exclusion"], text) for text in (raw, scope)):
        if jurisdiction.state == "DC":
            return "unknown", CONFLICT_REASON
        return "elsewhere", "The stated remote policy explicitly excludes the District of Columbia."
    for text in (raw, scope):
        match = re.fullmatch(rules["country_only"], text)
        if match:
            restriction = _parse_worksite(match[1] or match[2], gazetteer)
            if _place(restriction, gazetteer)[0] == "elsewhere":
                if _jurisdictions_conflict(restriction, jurisdiction):
                    return "unknown", CONFLICT_REASON
                return "elsewhere", "The stated remote policy limits work to an explicitly nonlocal jurisdiction."
    if any(re.search(rules["restrictions"], text) for text in (raw, scope)):
        return "remote_eligibility_unknown", "The remote policy has restrictions whose effect on DC is unresolved."
    candidates = (scope,) if scope else (raw,)
    if scope in gazetteer["dc_aliases"] or any(
        re.fullmatch(pattern, text) for text in candidates for pattern in rules["eligible_scopes"]
    ):
        return "remote_us_eligible", "The stated remote scope includes workers in the District of Columbia."
    return "remote_eligibility_unknown", "Remote work is stated without clear eligibility for DC residents."


def _worksites_conflict(display, structured, result, gazetteer):
    if _jurisdictions_conflict(display, structured):
        return True
    # An unrelated, nonlocal city cannot disambiguate a possibly nearby name.
    return (
        result[0] == "elsewhere"
        and bool(display.locality and structured.locality and display.locality != structured.locality)
        and (display.state in ("MD", "VA") or display.locality in gazetteer["ambiguous_names"])
        and _place(display, gazetteer)[0] == "unknown"
    )


def _classify_geo(location_raw, context, gazetteer):
    raw = _normalize(location_raw)
    offered = _parts(location_raw)
    display = [_parse_worksite(part, gazetteer) for part in offered]
    jurisdiction = _structured_jurisdiction(context, gazetteer)
    structured = list(_structured_worksites(context, gazetteer))
    if (jurisdiction.conflict or any(site.conflict for site in display)
            or any(site.conflict for site, _ in structured)):
        return "unknown", CONFLICT_REASON
    if display and all(_jurisdictions_conflict(site, jurisdiction) for site in display):
        return "unknown", CONFLICT_REASON
    if display and structured and all(
        _worksites_conflict(site, other, result, gazetteer)
        for site in display for other, result in structured
    ):
        return "unknown", CONFLICT_REASON
    if structured and all(_jurisdictions_conflict(other, jurisdiction) for other, _ in structured):
        return "unknown", CONFLICT_REASON

    other = _choose_worksite([result for _, result in structured])
    remote = _remote_result(raw, context, gazetteer)
    if remote:
        if remote[0] == "elsewhere" and other[0] in LOCAL | {"remote_us_eligible"}:
            return "unknown", CONFLICT_REASON
        # A separate, explicit local worksite remains evidence when remote eligibility is unclear.
        if remote[0] == "remote_eligibility_unknown":
            physical = [_place(site, gazetteer) for part, site in zip(offered, display)
                        if not _remote_result(_normalize(part), {}, gazetteer)]
            local = _choose_worksite(physical)
            if local[0] in LOCAL and _arrangement_allows_worksite(context, raw, gazetteer):
                if other[0] == "elsewhere":
                    return "unknown", CONFLICT_REASON
                return local
        return remote

    if len(display) <= 1 and (jurisdiction.state or jurisdiction.country):
        display = [_with_jurisdiction(display[0] if display else _Worksite(), jurisdiction)]
    result = _choose_worksite([_place(site, gazetteer) for site in display])
    if structured:
        if result[0] == "unknown" and other[0] != "unknown":
            result = other[0], "Structured worksite evidence resolves the display location. " + other[1]
        elif result[0] == "elsewhere" and other[0] != "elsewhere":
            return "unknown", "The structured worksite evidence conflicts with or qualifies the nonlocal display location."
        elif result[0] in LOCAL and other[0] == "elsewhere":
            return "unknown", CONFLICT_REASON

    if result[0] in LOCAL:
        title = context.get("title")
        title = title if isinstance(title, str) else ""
        suffix = re.split(r"\s+[-–—]\s+", title)[-1] if re.search(r"\s+[-–—]\s+", title) else ""
        if _normalize(suffix).removesuffix(" opportunities") in gazetteer["conflicting_title_places"]:
            return "unknown", "The location and the explicit nonlocal worksite suffix in the title conflict."
    return result


def _arrangement_allows_worksite(context, raw, gazetteer):
    return (_normalize(context.get("work_arrangement")) not in ("remote", "fully remote")
            and not re.search(gazetteer["remote"]["restrictions"], raw))


def classify_geo(location_raw, context=None):
    """Return (classification, explanation) using only posting-level location evidence.

    Context can contain state, country, explicit worksites (strings or mappings
    with location, state and country), work_arrangement, remote_scope, or title. Employer headquarters is not a worksite.
    """
    return _classify_geo(location_raw, context or {}, load_gazetteer())
