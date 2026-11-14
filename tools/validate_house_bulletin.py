"""Offline precision/recall and per-announcement span comparison against reviewed PDFs."""
import argparse
import json
from pathlib import Path

from career_engine.house_bulletin import digest, pdf_layout, reconcile_layout
from career_engine.registry_state import atomic_json

MANIFEST = Path(__file__).resolve().parents[1] / "tests/fixtures/house_bulletin/manifest.json"


def validate(directory):
    cases, sets = [], []
    for fixture in json.loads(MANIFEST.read_text())["fixtures"]:
        body = (Path(directory) / fixture["file"]).read_bytes()
        if digest(body) != fixture["sha256"]:
            raise ValueError("fixture_sha256_mismatch:" + fixture["file"])
        layout, pages, warning = pdf_layout(body)
        data = reconcile_layout(layout, pages, warning)
        expected = fixture["spans"]
        observed = {r["source_id"]: r for r in data["records"]}
        hits = expected.keys() & observed.keys()
        sets.append(set(observed))
        span_differences, matches = [], []
        for mem in expected:
            if mem not in observed:
                continue
            spans = observed[mem]["raw_sections"]
            actual = [[s["start"]["page"], s["end_page"]] for s in spans]
            exact = all(layout[s["start"]["offset"]:s["end_offset_exclusive"]] == s["raw_text"]
                        and s["raw_text"].startswith(mem) for s in spans)
            if actual != [expected[mem]] or not exact:
                span_differences.append({"id": mem, "expected": expected[mem], "actual": actual, "exact_text_slice": exact})
            else:
                matches.append({"id": mem, "pages": expected[mem], "raw_span_sha256": digest(spans[0]["raw_text"].encode())})
        cases.append({
            "source": fixture["url"], "artifact_sha256": fixture["sha256"], "issue_date": data["issue_date"],
            "expected_issue_date": fixture["issue_date"], "expected_pages": fixture["pages"], "extracted_pages": data["extracted_pages"],
            "expected_ids": len(expected), "observed_occurrences": len(data["occurrences"]),
            "retained_ids": len(observed), "precision": len(hits) / len(observed) if observed else 0,
            "recall": len(hits) / len(expected), "missed_ids": sorted(expected.keys() - observed.keys()),
            "unexpected_ids": sorted(observed.keys() - expected.keys()), "span_discrepancies": span_differences,
            "verified_spans": matches, "completeness": data["completeness"], "diagnostics": data["diagnostics"],
            "field_unknown_counts": {k: sum(not r[k] for r in observed.values())
                                     for k in ("advertised_title", "office", "advertised_worksite",
                                               "application_instructions", "published_emails", "application_deadline")},
        })
    return {"scope": "historical development PDFs; announcement-level metrics, not field accuracy or current coverage",
            "fixtures": cases, "cross_issue_ids": sorted(set.intersection(*sets)),
            "annual_unique_ids": len(set.union(*sets)),
            "passed": all(c["precision"] == c["recall"] == 1 and not c["span_discrepancies"]
                          and c["issue_date"] == c["expected_issue_date"] and c["extracted_pages"] == c["expected_pages"]
                          for c in cases)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("fixture_dir", type=Path)
    p.add_argument("--output", type=Path)
    args = p.parse_args(argv)
    result = validate(args.fixture_dir)
    if args.output:
        atomic_json(args.output, result)
    print(json.dumps({k: v for k, v in result.items() if k != "fixtures"}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
