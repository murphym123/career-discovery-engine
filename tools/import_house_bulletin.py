"""Manually import local House Employment Bulletin PDFs and exported EML files."""
import argparse
import csv
import json
from pathlib import Path
from uuid import uuid4

from career_engine.collection_health import collection_health, health_markdown
from career_engine.house_bulletin import HOUSE_PAGE, SOURCE
from career_engine.house_bulletin_intake import import_bulletins
from career_engine.markets import DC_MARKET
from career_engine.publication_audit import write_ledger
from career_engine.registry_state import atomic_json

CSV_FIELDS = (
    "identity", "source", "source_id", "source_type", "title", "company", "office", "office_kind",
    "location", "advertised_worksite", "geography", "url", "index_url", "issue_date", "posted_at",
    "closing_at", "retrieved_at", "first_observed_at", "last_observed_at", "observed_issue_dates",
    "cross_issue_republication", "duplicate_status", "content_versions", "record_kind", "evidence_status",
    "detail_status", "document_completeness", "description", "application_instructions", "published_emails", "application_emails",
    "application_deadline", "source_artifact", "sha256", "source_urls", "raw_sections", "source_sightings",
)


def publish_import(root, capture, records, ledger_count):
    folder = Path(root) / "imports" / uuid4().hex
    folder.mkdir(parents=True, mode=0o700)
    documents, messages = capture["documents"], capture["messages"]
    diagnostics = list(capture["input_diagnostics"])
    observed = set()
    for sha, doc in documents.items():
        observed.update(doc["audit"]["unique_observed_ids"])
        diagnostics.extend({"artifact_sha256": sha, **item} for item in doc["audit"]["diagnostics"])
    for sha, mail in messages.items():
        diagnostics.extend({"message_sha256": sha, **item} for item in mail["diagnostics"])
        for attachment in mail["attachments"]:
            if not attachment["source_artifact"]:
                diagnostics.extend({"message_sha256": sha, "part": attachment["part"], **item}
                                   for item in attachment["diagnostics"])
    retained = {row["source_id"] for row in records}
    complete = bool(records) and not diagnostics and observed == retained
    status = "RECONCILED OBSERVED PDF TEXT" if complete else "PARTIAL COLLECTION"
    stats = {SOURCE: {"status": "ok" if complete else "partial_failure", "unique": len(records),
                      "errors": sorted({item["code"] for item in diagnostics})}}
    health = collection_health(stats)
    at = capture["observed_at"]
    data = {"schema_version": "job-source-audit/v1", "kind": "UNRANKED LOCAL HOUSE BULLETIN EVIDENCE",
            "captured_at": at, "market": DC_MARKET.asdict(),
            "collection_context": {"mode": "manual_local_pdf_or_eml", "current_coverage_verified": False},
            "completeness": status, "collection_health": health, "stats": stats, "records": records,
            "documents": {sha: {**doc, "audit": {k: v for k, v in doc["audit"].items() if k != "records"}}
                          for sha, doc in documents.items()},
            "messages": messages, "diagnostics": diagnostics, "unique_observed_pdf_ids": sorted(observed),
            "retained_ids": sorted(retained), "unresolved_pdf_ids": sorted(observed - retained),
            "ledger_unique_ids": ledger_count, "evidence_path_base": "../..",
            "limits": "Reconciliation covers observed text only. Current vacancies, omitted issues and publisher inventory are unknown."}
    atomic_json(folder / "house-bulletin.json", data)
    with (folder / "house-bulletin.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in records:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value
                             for key in CSV_FIELDS if (value := row.get(key)) is not None})
    lines = ["# House Employment Bulletin import", "", status, "", data["limits"], "",
             f"PDF IDs observed: {len(observed)}. IDs retained: {len(retained)}. Annual ledger IDs: {ledger_count}.",
             "", health_markdown(health), "", "| Announcement | Bulletin issue | Source pages | Original PDF |",
             "|---|---|---|---|"]
    for row in records:
        first = row["raw_sections"][0]["start"]["page"]
        last = row["raw_sections"][0]["end_page"]
        lines.append(f"| {row['source_id']} | {row['issue_date'] or 'unknown'} | {first}–{last} | "
                     f"[Local source](../../{row['source_artifact']}#page={first}) |")
    lines.extend(["", "Raw text, occurrence positions, source hashes, application passages and diagnostic evidence are in JSON."])
    (folder / "house-bulletin.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    retrieval = "searched" if complete else "partially_retrieved"
    write_ledger(folder / "coverage-audit.json", {
        "schema_version": "publication-coverage-ledger/v1", "captured_at": at, "market": DC_MARKET.asdict(),
        "summary": {"discovered_source_candidates": 1, "retrieval_states": {retrieval: 1}},
        "sources": [{"id": SOURCE, "url": HOUSE_PAGE, "source": SOURCE, "source_kind": "manual_house_bulletin",
                     "retrieval_state": retrieval, "verification": "local_source_bytes_and_text_only",
                     "reason": data["limits"], "errors": stats[SOURCE]["errors"],
                     "inventory": {"scope": "unique MEM IDs in supplied PDF text; email evidence reviewed separately",
                                   "metadata_complete": complete, "metadata_retained": len(retained),
                                   "observed_available_items": len(observed) if observed else None,
                                   "publisher_inventory_total": None, "descriptions_observed": len(records),
                                   "source_artifact_sha256s": sorted(documents)}}],
        "ecosystem_gaps": [], "unknown_boundary": data["limits"],
    })
    return {"status": status, "retained": len(records), "ledger_unique_ids": ledger_count,
            "diagnostic_codes": stats[SOURCE]["errors"], "import_folder": str(folder)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="Local .pdf or .eml paths")
    parser.add_argument("--output-dir", type=Path, default=Path("output/house-bulletin"))
    parser.add_argument("--source-url", default="", help="Optional public attribution for a single PDF; never fetched")
    args = parser.parse_args(argv)
    try:
        result = import_bulletins(args.inputs, args.output_dir,
            lambda capture, rows, count: publish_import(args.output_dir, capture, rows, count), args.source_url)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"status": "IMPORT FAILED", "error": str(exc) if isinstance(exc, ValueError) else type(exc).__name__}))
        return 1
    print(json.dumps(result))
    return 2 if result["status"] == "PARTIAL COLLECTION" else 0


if __name__ == "__main__":
    raise SystemExit(main())
