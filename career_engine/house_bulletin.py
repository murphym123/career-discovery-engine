"""Offline House bulletin text extraction and source-bound MEM reconciliation."""
import bisect
import hashlib
import os
import re
import selectors
import subprocess
import tempfile
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

from career_engine.discovery import geography

SOURCE = "house_bulletin"
HOUSE_PAGE = "https://www.house.gov/employment/positions-with-members-and-committees"
MAX_PDF = 16_000_000
MAX_PAGES = 100
MAX_TEXT = 4_000_000
DASH = r"[-\u2010-\u2015]"
MEM = re.compile(rf"(?<![\w-])MEM\s*{DASH}\s*(\d{{3}})\s*{DASH}\s*(\d{{2}})(?![\w-])", re.I)
MEM_LIKE = re.compile(rf"\bMEM\s*{DASH}(?=\s*[\w])", re.I)
EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
WEEK = re.compile(r"(?im)^\s*Week of\s+([A-Za-z]+)[ \t]+(\d{1,2}),[ \t]+(\d{4})[ \t]*$")
MONTHS = {name.lower(): i for i, name in enumerate(
    "January February March April May June July August September October November December".split(), 1)}
MONTHS.update({name[:3]: i for name, i in list(MONTHS.items())})
DATE = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?"
APPLY = re.compile(r"\b(?:how to apply|applicant instructions|to apply\s*[:,]|please\s+(?:submit|send|email)|"
                   r"(?:interested|qualified)\s+(?:applicants|candidates|individuals)\s+"
                   r"(?:should|must|are encouraged to)\s+(?:submit|send|email))", re.I)
TITLE_ROLE = re.compile(r"\b(?:assistant|associate|advisor|analyst|counsel|director|manager|secretary|"
                        r"caseworker|liaison|advocate|scheduler|correspondent|coordinator|member|intern|"
                        r"specialist|clerk|officer|aide|fellow|driver|chief|representative|creator)\b", re.I)


def digest(body):
    return hashlib.sha256(body).hexdigest()


class PDFReadError(ValueError):
    """Stable diagnostic code, with no local path or PDF metadata in logs."""


def _poppler(args, limit, timeout=30):
    """Read bounded stdout/stderr; timeout and kill the child on every failure."""
    try:
        child = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise PDFReadError("poppler_unavailable") from None
    output, errors = bytearray(), bytearray()
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ, (output, limit))
            selector.register(child.stderr, selectors.EVENT_READ, (errors, 65536))
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PDFReadError("pdf_extraction_timeout")
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    dest, cap = key.data
                    dest.extend(chunk)
                    if len(dest) > cap:
                        raise PDFReadError("pdf_text_or_diagnostic_limit")
        if child.wait(timeout=max(0.01, deadline - time.monotonic())):
            raise PDFReadError("pdf_unreadable_or_corrupt")
    except subprocess.TimeoutExpired:
        raise PDFReadError("pdf_extraction_timeout") from None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        child.stdout.close()
        child.stderr.close()
    return bytes(output), bool(errors.strip())


def pdf_layout(body):
    if not body or len(body) > MAX_PDF:
        raise PDFReadError("pdf_empty_or_size_limit")
    if not body.startswith(b"%PDF-"):
        raise PDFReadError("not_a_pdf")
    with tempfile.TemporaryDirectory(prefix="house-bulletin-") as folder:
        path = Path(folder) / "document.pdf"
        path.write_bytes(body)
        info, info_warning = _poppler(["pdfinfo", str(path)], 65536)
        pages = re.search(rb"(?m)^Pages:\s+(\d+)", info)
        if not pages or not 1 <= int(pages[1]) <= MAX_PAGES:
            raise PDFReadError("pdf_missing_page_count_or_page_limit")
        if re.search(rb"(?mi)^Encrypted:\s+yes", info):
            raise PDFReadError("encrypted_pdf_unsupported")
        text, text_warning = _poppler(["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"], MAX_TEXT)
    return text.decode("utf-8", errors="replace"), int(pages[1]), info_warning or text_warning


def _date(month, day, year):
    try:
        return date(int(year), MONTHS[month.lower().rstrip(".")], int(day)).isoformat()
    except (KeyError, ValueError):
        return None


def _flat(text):
    return re.sub(r"\s+", " ", text).strip()


def _unique(values):
    return list(dict.fromkeys(values))


def _fields(body, inline_title=""):
    # Prose fields are evidence excerpts, not rewritten or inferred from an ID.
    flat = _flat(body)
    first = flat[:1000]
    opening = _flat(re.split(r"\n[ \t]*\n", body, maxsplit=1)[0])
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    labeled_title = re.search(r"(?im)^\s*(?:position available|position title|job title|title):[ \t]*(.+)$", body)
    title = inline_title or (labeled_title[1].strip() if labeled_title else None)
    if not title:
        for line in lines[:5]:
            candidate = re.split(r"\s*(?:\||[–—-]\s+(?:Office of|The Committee)\b|(?: Role)? for (?:Rep\.|Representative|Congress))", line, maxsplit=1)[0]
            if (len(candidate) <= 100 and TITLE_ROLE.search(candidate)
                    and not re.search(r"[.!?;:]|\b(?:seeks|seeking|looking|responsible|House of Representatives|Committee on|Office of)\b", candidate, re.I)
                    and (candidate.isupper() or re.match(r"[A-Z]", candidate) and len(candidate.split()) <= 10
                         and not any(word[0].islower() for word in candidate.split() if word.isalpha() and word.lower() not in ("of", "and", "to", "the", "for", "or", "on")))):
                title = candidate
                break
    if not title:
        candidate = re.search(r"\b(?:seeks|is seeking|seeking|to hire|looking for) (?:a|an) "
                              r"(?:[a-z][a-z,-]* ){0,9}([A-Z][A-Za-z/-]*(?:(?: (?:of|and/or))? [A-Z][A-Za-z/-]*){0,5})\b", first)
        if candidate and TITLE_ROLE.search(candidate[1]):
            title = re.split(r"\s+(?:The|This|Our|They)\s+", candidate[1], maxsplit=1)[0]

    office_candidates = []
    for kind, pattern in [
        ("member", r"\b(?:Office of (?:U\.S\. )?)?(?:Congresswoman|Congressman|Representative|Rep\.|Resident Commissioner(?: of Puerto Rico,)?)\s+[A-Z][\w’'-]+(?:\s+[A-Z][\w’'-]+){0,3}(?:\s*\([A-Z]{2}[-–]?\d{1,2}\))?"),
        ("committee", r"\b(?:House )?(?:Select |Standing )?Committee on .{3,105}?(?=\s*\(|,|\s+(?:seeks|is seeking)\b|\n|$)"),
        ("other_house_office", r"\bOffice of the (?:General Counsel|Clerk|Chief Administrative Officer|Sergeant at Arms|Inspector General|Historian)\b"),
    ]:
        match = re.search(pattern, body[:1100])
        if match:
            office_candidates.append((match.start(), kind, _flat(match[0])))
    office_label = re.search(r"(?im)^Office:[ \t]*(.+)$", body)
    if office_label:
        label = office_label[1].strip()
        if re.search(r"(?i)\b(?:sub)?committee\b", label):
            office_candidates.append((-1, "committee", label))
    _, office_kind, office = min(office_candidates, default=(0, "unknown", None))

    places = []
    label = re.search(r"(?im)^\s*(?:office/location|work location|location|worksite|duty station|district office\s*[-–—])[ \t]*:?[ \t]+(.+)$", body)
    if label:
        places.append(label[1].strip())
    # Require actual worksite wording, not a member district, travel, or a
    # reference to DC colleagues. Retain the spelling printed in the source.
    city = r"(?:Washington\s*,?\s*D\.?C\.?|District of Columbia|D\.C\.|DC|[A-Z][\w’'-]+(?: [A-Z][\w’'-]+){0,3}, [A-Z][A-Za-z]+)"
    for match in re.finditer(rf"(?i:\b(?:based in|serve in|work in|position in|in (?:our|his|her|the)|for (?:our|his|her|their|the)))\s+({city})(?=\s*,?\s*(?i:office|team|and|with|occasionally)\b|[.;\n]|$)", first):
        places.append(match[1].strip())
    district = re.search(r"(?i:\b(?:for the|based in the)) ([A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+){0,2}) (?i:office)\b", opening)
    if district:
        places.append(district[1])
    for match in re.finditer(rf"\b({city})[-–](?i:based)\s+(?i:office|team|position)\b", first):
        places.append(match[1])
    places = _unique(places)
    # A specific labeled location is preferred only if alternatives agree.
    labels = {"dc_dmv" if re.fullmatch(r"D\.?C\.?", v, re.I) else geography(v) for v in places}
    worksite = places[0] if len(places) == 1 or places and labels == {"dc_dmv"} else None
    geo = next(iter(labels)) if worksite else "unknown"
    if geo == "elsewhere" and "," not in worksite:  # A city alone may be ambiguous.
        geo = "unknown"

    app = APPLY.search(body)
    instructions = body[app.start():].strip() if app else None
    deadline_candidates = []
    for match in re.finditer(rf"(?i)\b(?:closing date\s*:|(?:application )?deadline\s*:|apply by|no later than|"
                             rf"last day to (?:apply|submit an application) is)\s*(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?({DATE}|until filled|rolling(?: basis)?)", flat):
        deadline_candidates.append(match[1].strip())
    for match in re.finditer(rf"(?i)\bby\s+(?:(?:COB|EOD)\s+(?:on\s+)?|[0-9:]+\s*(?:AM|PM)\s+on\s+)?(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?({DATE})",
                             _flat(instructions or "")):
        deadline_candidates.append(match[1].strip())
    deadline_candidates = _unique(deadline_candidates)
    deadline = deadline_candidates[0] if len(deadline_candidates) == 1 else None
    parts = re.fullmatch(r"([A-Za-z.]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", deadline or "", re.I)
    closing = _date(*parts.groups()) if parts else None
    return {"advertised_title": title, "office": office, "office_kind": office_kind,
            "advertised_worksite": worksite, "worksite_evidence": places,
            "location": worksite or "", "raw_location": worksite or "", "geography": geo,
            "application_instructions": instructions, "published_emails": _unique(EMAIL.findall(body)),
            "application_emails": _unique(EMAIL.findall(instructions or "")),
            "application_deadline": deadline, "deadline_evidence": deadline_candidates, "closing_at": closing or ""}


def reconcile_layout(layout, expected_pages=None, poppler_warning=False):
    """Positions use one-based pages/lines and zero-based text offsets."""
    pages = layout.split("\f")
    if pages[-1] == "" and len(pages) > 1:
        pages.pop()
    starts = [0]
    for page in pages[:-1]:
        starts.append(starts[-1] + len(page) + 1)

    def pos(offset):
        page_index = bisect.bisect_right(starts, offset) - 1
        return {"offset": offset, "page": page_index + 1,
                "line": layout.count("\n", starts[page_index], offset) + 1}

    def span(a, b):
        return {"start": pos(a), "end_offset_exclusive": b,
                "end_page": pos(max(a, b - 1))["page"], "raw_text": layout[a:b]}

    issues = []
    if expected_pages is not None and len(pages) != expected_pages:
        issues.append({"code": "page_count_mismatch", "expected": expected_pages, "extracted": len(pages)})
    empty = [i for i, page in enumerate(pages, 1) if not page.strip()]
    if empty:
        issues.append({"code": "pages_without_text_no_ocr", "pages": empty})
    if poppler_warning or re.search(r"[\x00-\x08\x0b\x0e-\x1f\ufffd]", layout):
        issues.append({"code": "pdf_extraction_warning_or_suspicious_text"})
    dates = _unique(_date(*m.groups()) for m in WEEK.finditer(layout))
    issue_date = dates[0] if len(dates) == 1 else None
    if not issue_date and expected_pages is not None:
        issues.append({"code": "issue_date_unknown_or_conflicting"})

    line_end_re = re.compile(r"[\n\f]")
    hits, headings, all_ids = [], [], set()
    for m in MEM.finditer(layout):
        mem = f"MEM-{m[1]}-{m[2]}"
        all_ids.add(mem)
        a, b = m.span()
        before = max(layout.rfind("\n", 0, a), layout.rfind("\f", 0, a)) + 1
        boundary = line_end_re.search(layout, b)
        after = boundary.start() if boundary else len(layout)
        left, right = layout[before:a].strip(), layout[b:after].strip()
        inline = re.fullmatch(r"(?:[:–—]|-\s+)\s*(\S.{0,159})", right)
        # A blank line separates headings from the previous posting.
        prev = re.split(r"[\n\f]", layout[max(0, before - 150):max(0, before - 1)])[-1]
        reference_context = bool(re.search(r"(?i)\b(?:subject(?: line)?|reference(?: number)?|quote|mention|use|include)\s*[:“\"']?\s*$", prev))
        kind = "reference"
        if not left and (not right or inline) and not reference_context:
            kind = "heading"
            headings.append((mem, a, b, inline[1] if inline else "", after))
        elif not left and not reference_context:
            kind = "ambiguous_boundary"
            issues.append({"code": "ambiguous_mem_boundary", "id": mem, **pos(a)})
        hits.append({"id": mem, "kind": kind, "end_offset_exclusive": b,
                     "end_page": pos(b - 1)["page"], "raw_id": m[0], **pos(a)})
    if expected_pages is not None:
        glyphs = list(re.finditer(r"(?i)\b(?:ofice|staf|efective|eforts|qualifcations|profciency|suficient)\b", layout))
        if glyphs:
            issues.append({"code": "possible_text_layer_glyph_loss", "count": len(glyphs),
                           "examples": [{"word": m[0], **pos(m.start())} for m in glyphs[:10]]})
        for match in re.finditer(r"(?im)^\s*(?:internship openings|other openings)\s*$", layout):
            issues.append({"code": "unreconciled_non_mem_section", **pos(match.start())})
    starts_seen = {h["offset"] for h in hits}
    for m in MEM_LIKE.finditer(layout):
        if m.start() not in starts_seen:
            end = line_end_re.search(layout, m.start())
            b = end.start() if end else len(layout)
            issues.append({"code": "malformed_mem_token", **span(m.start(), b)})
    grouped = defaultdict(list)
    for i, (mem, start, end, inline, line_end) in enumerate(headings):
        stop = headings[i + 1][1] if i + 1 < len(headings) else len(layout)
        stop = start + len(layout[start:stop].rstrip())
        body = layout[line_end:stop].strip()
        if not body and not inline:
            issues.append({"code": "empty_numbered_section", "id": mem, **pos(start)})
        grouped[mem].append({**span(start, stop), "fields": _fields(body, inline)})
    missing = sorted(all_ids - grouped.keys())
    if missing:
        issues.append({"code": "observed_ids_without_numbered_sections", "ids": missing})
    if not grouped:
        issues.append({"code": "no_mem_sections"})
    if issue_date and any(mem[-2:] != issue_date[2:4] for mem in all_ids):
        issues.append({"code": "issue_date_id_year_mismatch"})
    if headings:
        preface = layout[:headings[0][1]]
        leftover = re.sub(r"(?im)^\s*Member and Committee Openings\s*$", "", WEEK.sub("", preface)).strip()
    else:
        preface, leftover = layout, ""
    if leftover:
        issues.append({"code": "unassigned_preface_material"})
    rows = []
    for mem, sections in grouped.items():
        if len(sections) > 1:
            issues.append({"code": "duplicate_numbered_sections", "id": mem, "count": len(sections)})
        fields = sections[0]["fields"]
        rows.append({"source": SOURCE, "source_id": mem, "identity": f"{SOURCE}:{mem}",
                     "announcement_id": mem, "id_year_suffix": mem[-2:], "issue_date": issue_date,
                     "title": fields["advertised_title"] or f"House announcement {mem}",
                     "company": fields["office"] or "U.S. House of Representatives (office unspecified)",
                     "description": sections[0]["raw_text"], "raw_sections": sections, **fields,
                     "description_sha256": digest(sections[0]["raw_text"].encode()),
                     "content_sha256": digest("\n".join(_flat(s["raw_text"]) for s in sections).encode()),
                     "description_truncated": False, "record_kind": "advertised_position",
                     "detail_status": "original_pdf_text_span_observed", "posted_at": "",
                     "evidence_status": "historical_or_local_announcement_current_availability_unverified"})
    gaps = {}
    for year in sorted({mem[-2:] for mem in grouped}):
        numbers = {int(mem[4:7]) for mem in grouped if mem[-2:] == year}
        gaps[year] = sorted(set(range(min(numbers), max(numbers) + 1)) - numbers)
    return {"issue_date": issue_date, "pdf_pages": expected_pages, "extracted_pages": len(pages),
            "occurrences": hits, "unique_observed_ids": sorted(all_ids), "retained_ids": list(grouped),
            "unresolved_ids": missing, "unassigned_preface": preface,
            "number_gaps_review_only": gaps, "diagnostics": issues, "records": rows,
            "completeness": "PARTIAL COLLECTION" if issues else "RECONCILED OBSERVED PDF TEXT"}
