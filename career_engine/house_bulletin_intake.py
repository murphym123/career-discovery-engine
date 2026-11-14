"""Local MIME/PDF receipts and an annual, append-evidence vacancy ledger."""
import fcntl
import json
import re
from datetime import timezone
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from career_engine.ats import stamp
from career_engine.extract import safe_public_url
from career_engine.house_bulletin import (
    HOUSE_PAGE, MAX_PDF, MEM, SOURCE, PDFReadError, digest, pdf_layout, reconcile_layout,
)
from career_engine.registry_state import atomic_bytes, parse_json

MAX_EML = 32_000_000
MAX_LEDGER = 64_000_000
MAX_INPUTS = 16
MAX_BATCH_BYTES = 64_000_000
MAX_BATCH_PDFS = 32
MAX_BATCH_TEXT = 12_000_000
MAX_PARTS = 64
MAX_ATTACHMENTS = 16
MAX_BODY_TEXT = 1_000_000
SCHEMA = "house-bulletin-vacancies/v1"


def public_pdf_url(value):
    """Keep public attribution only; no credentials, query parameters or fetch."""
    if not value or re.search(r"[\x00-\x20]", value) or not safe_public_url(value):
        return ""
    url = urlsplit(value)
    return value if not url.query and not url.fragment and unquote(url.path).lower().endswith(".pdf") else ""


def _mail_date(value):
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        return parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


class _MailHTML(HTMLParser):
    """Read visible text and anchor hrefs; never render or load a resource."""
    BLOCKS = {"br", "div", "p", "li", "tr", "h1", "h2", "h3", "hr", "pre", "section"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links, self.hidden = [], [], []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "template", "head"}:
            self.hidden.append(tag)
        if self.hidden:
            return
        if tag in self.BLOCKS:
            self.parts.append("\n")
        if tag == "a":
            self.links.append(dict(attrs).get("href") or "")

    def handle_endtag(self, tag):
        if self.hidden and tag == self.hidden[-1]:
            self.hidden.pop()
        if not self.hidden and tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _body_link(url):
    url = public_pdf_url(url)
    if url and urlsplit(url).hostname == "content.govdelivery.com" and re.match(
            r"^/+attachments/USCAOHOUSE/", urlsplit(url).path):
        return url
    return ""


def read_email(body):
    """Parse only local MIME leaves. No nested messages, URLs, or executable parts."""
    try:
        message = BytesParser(policy=policy.default).parsebytes(body)
    except (ValueError, RecursionError):
        return {"diagnostics": [{"code": "unreadable_eml"}], "attachments": [], "body_parts": []}
    notices, attachments, bodies = [], [], []
    message_id = str(message.get("Message-ID") or "").strip()
    sent_header = str(message.get("Date") or "")
    # Received headers are newest first. Only the parsed timestamp is exported.
    received = next((v for line in message.get_all("Received", [])
                     if (v := _mail_date(str(line).rsplit(";", 1)[-1]))), None)
    parent = {"message_identity": digest(message_id.encode()) if message_id else digest(body),
              "message_identity_basis": "message_id_sha256" if message_id else "eml_artifact_sha256",
              "date_header": sent_header if _mail_date(sent_header) else None,
              "sent_at": _mail_date(sent_header), "received_at": received}
    if not parent["sent_at"]:
        notices.append({"code": "email_date_missing_or_unparseable"})
    stack = [(message, "1", 0)]
    visited = body_bytes = 0
    while stack:
        if visited >= MAX_PARTS:
            notices.append({"code": "mime_part_limit"})
            break
        part, number, depth = stack.pop()
        visited += 1
        if part.get_content_maintype() == "multipart" and part.is_multipart():
            if depth >= 8:
                notices.append({"code": "mime_depth_limit", "part": number})
                continue
            children = list(part.iter_parts())
            stack.extend((p, f"{number}.{i}", depth + 1) for i, p in reversed(list(enumerate(children, 1))))
            if part.defects:
                notices.append({"code": "mime_structure_defect", "part": number})
            continue
        name = part.get_filename() or ""
        kind = part.get_content_type()
        if kind == "application/pdf" or name.lower().endswith(".pdf"):
            if len(attachments) >= MAX_ATTACHMENTS:
                notices.append({"code": "pdf_attachment_limit", "part": number})
                continue
            payload = part.get_payload(decode=True) or b""
            cid = str(part.get("Content-ID") or "").strip()
            attachments.append({"part": number, "filename": name, "content_type": kind,
                                "content_id_sha256": digest(cid.encode()) if cid else None,
                                "body": payload})
        elif kind.startswith("message/") or part.get_content_disposition() == "attachment" or name:
            notices.append({"code": "unsupported_mime_attachment_not_processed", "part": number,
                            "content_type": kind})
        elif kind in {"text/plain", "text/html"}:
            payload = part.get_payload(decode=True) or b""
            if body_bytes + len(payload) > MAX_BODY_TEXT:
                notices.append({"code": "email_body_size_limit", "part": number})
                continue
            body_bytes += len(payload)
            try:
                text = payload.decode(part.get_content_charset() or "utf-8")
            except (LookupError, UnicodeError):
                text = payload.decode("utf-8", errors="replace")
                notices.append({"code": "email_body_encoding_review", "part": number})
            links = re.findall(r"https?://[^\s<>\x22]+", text) if kind == "text/plain" else []
            if kind == "text/html":
                parser = _MailHTML()
                try:
                    parser.feed(text)
                    parser.close()
                    text = "".join(parser.parts)
                    links = parser.links
                except (ValueError, RecursionError):
                    notices.append({"code": "email_html_unparseable", "part": number})
                    continue
            bodies.append({"part": number, "content_type": kind, "text": text,
                           "public_attachment_links": sorted({u for link in links if (u := _body_link(link))})})
        if part.defects:
            notices.append({"code": "mime_part_decode_defect", "part": number})
    if not attachments:
        notices.append({"code": "no_pdf_attachments_remote_links_not_fetched"})
    return {**parent, "diagnostics": notices, "attachments": attachments, "body_parts": bodies}


def reconcile_email(email, attachments):
    pdf_ids = {mem for doc in attachments for mem in doc["audit"]["unique_observed_ids"]}
    body_ids = set()
    sections_by_id = {}
    for doc in attachments:
        for row in doc["audit"]["records"]:
            sections_by_id.setdefault(row["source_id"], []).extend(
                re.sub(r"\s+", " ", s["raw_text"]).strip() for s in row["raw_sections"])
    review, parts = list(email["diagnostics"]), []
    body_section_differs = set()
    for part in email["body_parts"]:
        parsed = reconcile_layout(part["text"])
        ids = parsed["unique_observed_ids"]
        body_ids.update(ids)
        if any(i["code"] in {"malformed_mem_token", "ambiguous_mem_boundary", "duplicate_numbered_sections"}
               for i in parsed["diagnostics"]):
            review.append({"code": "email_body_mem_structure_review", "part": part["part"]})
        for row in parsed["records"]:
            for section in row["raw_sections"]:
                mem = row["source_id"]
                body_text = re.sub(r"\s+", " ", section["raw_text"]).strip()
                if mem in sections_by_id and not any(body_text in pdf for pdf in sections_by_id[mem]):
                    body_section_differs.add(mem)
        parts.append({**part, "occurrences": parsed["occurrences"], "unique_ids": ids})
    if body_ids - pdf_ids:
        review.append({"code": "email_body_ids_not_in_pdf_attachments", "ids": sorted(body_ids - pdf_ids)})
    if body_ids and pdf_ids - body_ids:
        review.append({"code": "pdf_ids_not_in_numbered_email_body", "ids": sorted(pdf_ids - body_ids)})
    if body_section_differs:
        review.append({"code": "email_body_section_differs_from_pdf", "ids": sorted(body_section_differs)})
    if not body_ids and any(part["text"].strip() for part in parts):
        review.append({"code": "unnumbered_email_body_not_reconciled_as_inventory"})
    return {k: v for k, v in email.items() if k not in {"body_parts", "attachments", "diagnostics"}} | {
        "body_parts": parts, "body_unique_ids": sorted(body_ids), "attachment_unique_ids": sorted(pdf_ids),
        "remote_resources_fetched": 0, "diagnostics": review}


def _read(path, cap):
    with path.open("rb") as file:
        data = file.read(cap + 1)
    if len(data) > cap:
        raise ValueError("input_size_limit")
    return data


def _archive(root, data, kind):
    sha = digest(data)
    relative = f"evidence/{sha}.{kind}"
    path = root / relative
    if path.is_symlink() or path.exists() and _read(path, len(data)) != data:
        raise ValueError("stored_source_evidence_hash_mismatch")
    if not path.exists():
        atomic_bytes(path, data)
    return {"sha256": sha, "bytes": len(data), "source_artifact": relative, "artifact_type": kind}


def _empty_audit(code):
    return {"issue_date": None, "pdf_pages": None, "extracted_pages": 0, "occurrences": [],
            "unique_observed_ids": [], "retained_ids": [], "unresolved_ids": [], "unassigned_preface": "",
            "number_gaps_review_only": {}, "records": [], "diagnostics": [{"code": code}],
            "completeness": "PARTIAL COLLECTION"}


def collect_inputs(paths, root, observed_at, source_url=""):
    docs, messages, problems = {}, {}, []
    total_input_bytes = total_text_bytes = 0

    def pdf(data, attachment=None, urls=()):
        nonlocal total_text_bytes
        sha = digest(data)
        code = ("pdf_empty_or_size_limit" if len(data) > MAX_PDF else
                "batch_pdf_limit" if sha not in docs and len(docs) >= MAX_BATCH_PDFS else "")
        if code:
            return {"artifact": {"sha256": digest(data), "bytes": len(data), "artifact_type": "pdf",
                                  "source_artifact": None},
                    "audit": _empty_audit(code), "source_urls": list(urls),
                    "attachments": [attachment] if attachment else []}
        artifact = _archive(root, data, "pdf")
        sha = artifact["sha256"]
        if sha not in docs:
            try:
                if total_text_bytes >= MAX_BATCH_TEXT:
                    raise PDFReadError("batch_pdf_text_limit")
                layout, pages, warning = pdf_layout(data)
                total_text_bytes += len(layout.encode())
                if total_text_bytes > MAX_BATCH_TEXT:
                    raise PDFReadError("batch_pdf_text_limit")
                audit = reconcile_layout(layout, pages, warning)
            except PDFReadError as exc:
                audit = _empty_audit(str(exc))
            docs[sha] = {"artifact": artifact, "audit": audit, "source_urls": [], "attachments": []}
        doc = docs[sha]
        doc["source_urls"] = list(dict.fromkeys(doc["source_urls"] + list(urls)))
        if attachment and attachment not in doc["attachments"]:
            doc["attachments"].append(attachment)
        return doc

    for n, path in enumerate(map(Path, paths), 1):
        kind = path.suffix.lower().lstrip(".")
        if kind not in {"eml", "pdf"}:
            problems.append({"code": "unsupported_input_type", "input_number": n})
            continue
        try:
            raw = _read(path, MAX_EML if kind == "eml" else MAX_PDF)
        except (OSError, ValueError) as exc:
            problems.append({"code": "input_unreadable" if isinstance(exc, OSError) else str(exc),
                             "input_number": n})
            continue
        total_input_bytes += len(raw)
        if total_input_bytes > MAX_BATCH_BYTES:
            problems.append({"code": "batch_input_byte_limit", "input_number": n,
                             "sha256": digest(raw), "bytes": len(raw)})
            continue
        if kind == "pdf":
            doc = pdf(raw, urls=[source_url] if source_url else [])
            if not doc["artifact"]["source_artifact"]:
                problems.extend({"input_number": n, **doc["artifact"], **issue}
                                for issue in doc["audit"]["diagnostics"])
            continue
        parent = _archive(root, raw, "eml")
        email = read_email(raw)
        linked = []
        attachment_audit = []
        for part in email["attachments"]:
            data = part["body"]
            edge = {"parent_message_sha256": parent["sha256"], **{k: v for k, v in part.items() if k != "body"}}
            # Only exact published attachment basenames link a URL to a part.
            urls = sorted({u for p in email["body_parts"] for u in p["public_attachment_links"]
                           if Path(unquote(urlsplit(u).path)).name == part["filename"]})
            doc = pdf(data, edge, urls)
            linked.append(doc)
            attachment_audit.append({**edge, **doc["artifact"], "diagnostics": doc["audit"]["diagnostics"]})
        messages[parent["sha256"]] = {
            "artifact": parent, "attachments": attachment_audit, **reconcile_email(email, linked)}
    return {"documents": docs, "messages": messages, "input_diagnostics": problems,
            "observed_at": observed_at}


def load_vacancy_ledger(root):
    path = root / "vacancy-ledger.json"
    if not path.exists():
        return {"schema_version": SCHEMA, "vacancies": {}, "documents": {}, "messages": {}}
    state = parse_json(_read(path, MAX_LEDGER))
    if not isinstance(state, dict) or state.get("schema_version") != SCHEMA or any(
            not isinstance(state.get(k), dict) for k in ("vacancies", "documents", "messages")):
        raise ValueError("invalid_vacancy_ledger")
    for key, entry in state["vacancies"].items():
        if not isinstance(entry, dict) or entry.get("announcement_id") != key or not isinstance(
                entry.get("sightings"), dict) or not entry["sightings"]:
            raise ValueError("invalid_vacancy_ledger_entry")
    return state


def _touch(entry, at):
    entry["first_observed_at"] = min(entry.get("first_observed_at", at), at)
    entry["last_observed_at"] = max(entry.get("last_observed_at", at), at)


def merge_observations(state, capture):
    at = capture["observed_at"]
    previously_seen = set(state["vacancies"])
    run_records = {}
    for sha, doc in capture["documents"].items():
        audit = doc["audit"]
        previous = state["documents"].setdefault(sha, {"artifact": doc["artifact"],
            "issue_date": audit["issue_date"], "source_urls": [], "attachments": []})
        _touch(previous, at)
        for k in ("source_urls", "attachments"):
            previous[k].extend(v for v in doc[k] if v not in previous[k])
        for row in audit["records"]:
            mem = row["source_id"]
            first_page = row["raw_sections"][0]["start"]["page"]
            url = next(iter(previous["source_urls"]), "")
            row = {**row, **doc["artifact"], "source_type": "house_employment_bulletin_pdf",
                   "source_urls": list(previous["source_urls"]), "url": f"{url}#page={first_page}" if url else "",
                   "index_url": HOUSE_PAGE, "retrieved_at": at,
                   "document_completeness": audit["completeness"]}
            vacancy = state["vacancies"].setdefault(mem, {"announcement_id": mem, "sightings": {}})
            _touch(vacancy, at)
            sighting = vacancy["sightings"].setdefault(sha, {"record": row})
            _touch(sighting, at)
            sighting["record"] = row
            run_records.setdefault(mem, set()).add(sha)
    for sha, email in capture["messages"].items():
        parent = state["messages"].setdefault(sha, {})
        parent.update({k: email.get(k) for k in ("artifact", "attachments", "message_identity",
                                               "message_identity_basis", "date_header", "sent_at", "received_at")})
        _touch(parent, at)

    rows = []
    for mem, current in run_records.items():
        vacancy = state["vacancies"][mem]
        sightings = vacancy["sightings"]
        issue_dates = sorted({v["record"]["issue_date"] for v in sightings.values() if v["record"]["issue_date"]})
        chosen = max(current, key=lambda k: (sightings[k]["record"]["issue_date"] or "", k))
        row = sightings[chosen]["record"]
        rows.append({**row, "first_observed_at": vacancy["first_observed_at"],
                     "last_observed_at": vacancy["last_observed_at"], "observed_issue_dates": issue_dates,
                     "cross_issue_republication": len(issue_dates) > 1,
                     "content_versions": len({v["record"]["content_sha256"] for v in sightings.values()}),
                     "duplicate_status": "previously_observed_annual_mem_id" if mem in previously_seen else "first_observed_annual_mem_id",
                     "source_sightings": [{"sha256": sha, "issue_date": v["record"]["issue_date"],
                        "source_artifact": v["record"]["source_artifact"], "raw_sections": v["record"]["raw_sections"],
                        "first_observed_at": v["first_observed_at"], "last_observed_at": v["last_observed_at"]}
                        for sha, v in sorted(sightings.items())]})
    return rows


def import_bulletins(paths, root, publish, source_url="", observed_at=None):
    """Keep input artifacts immutable; publish diagnostics before committing state."""
    if not 1 <= len(paths) <= MAX_INPUTS:
        raise ValueError("input_count_limit")
    if source_url and (len(paths) != 1 or Path(paths[0]).suffix.lower() != ".pdf"
                       or not public_pdf_url(source_url)):
        raise ValueError("source_url_requires_one_pdf_and_public_https_url_without_query")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / ".house-bulletin.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = load_vacancy_ledger(root)
        capture = collect_inputs(paths, root, observed_at or stamp(), source_url)
        rows = merge_observations(state, capture)
        body = (json.dumps(state, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
        if len(body) > MAX_LEDGER:
            raise ValueError("vacancy_ledger_size_limit")
        outcome = publish(capture, rows, len(state["vacancies"]))
        atomic_bytes(root / "vacancy-ledger.json", body)
        return outcome
