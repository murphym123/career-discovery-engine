"""Offline, fixed-budget prefix/stratified comparison on actual captured indexes.

No calls to job sources, scoring APIs, or the candidate-fit ranking model.
Counts describe evidence exposure, not relevance precision or market recall.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from career_engine.inspection import plan_inspections,early_signal,family
from career_engine.discovery import geography


def compare(data,budget=20,seed=5):
    records=data["records"]
    results={}
    for source in dict.fromkeys(row["source"] for row in records):
        rows=[row for row in records if row["source"]==source]
        byid={row["source"]+":"+row["source_id"]:row for row in rows}
        cases={}
        for strategy in ("prefix","diverse"):
            plan=plan_inspections(rows,budget,seed,strategy)
            chosen=[byid[choice["identity"]] for choice in plan]
            cases[strategy]={
                "planned":len(plan),"dc_dmv_selected":sum(geography(row.get("location", ""))=="dc_dmv" for row in chosen),
                "employers_selected":len({row["company"] for row in chosen if row.get("company") and row["company"]!="Confidential"}),
                "inspection_families":len({family(row) for row in chosen}),
                "weak_early_signals_selected":sum(early_signal(row) for row in chosen),
                "explicit_provider_early_selected":sum("provider_early_career" in row.get("experience_hint","") or "provider_internship" in row.get("experience_hint","") for row in chosen),
                "already_observed_descriptions":sum(row.get("description_observed",bool(row.get("description"))) for row in chosen),
                "strata":dict(Counter(choice["stratum"] for choice in plan)),"plan":plan,
            }
        old={choice["identity"] for choice in cases["prefix"]["plan"]}
        new={choice["identity"] for choice in cases["diverse"]["plan"]}
        results[source]={"records":len(rows),"dc_dmv_records":sum(geography(row.get("location", ""))=="dc_dmv" for row in rows),
                         **cases,"newly_inspected_identities":sorted(new-old),"prefix_only_identities":sorted(old-new)}
    return {"kind":"ACTUAL-INDEX INSPECTION EXPOSURE COMPARISON; NOT FIT PRECISION OR MARKET RECALL", "budget_per_source":budget,"seed":seed,"sources":results,
            "limitations":"Same captured indexes, same page cap. Provider early-career hints and title/function signals are weak metadata, not qualifications. Newly selected descriptions were actually retrieved; prefix-only descriptions may remain uninspected. No fit-quality comparison is inferred from missing labels."}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture",type=Path)
    parser.add_argument("--budget",type=int,default=20)
    parser.add_argument("--seed",type=int,default=5)
    parser.add_argument("--output",type=Path,default=Path("docs/issue5/inspection-comparison.json"))
    args=parser.parse_args();raw=args.capture.read_bytes()
    result=compare(json.loads(raw),args.budget,args.seed)
    result["capture_sha256"]=hashlib.sha256(raw).hexdigest()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({source:{"prefix_local":d["prefix"]["dc_dmv_selected"],"diverse_local":d["diverse"]["dc_dmv_selected"],"newly_selected":len(d["newly_inspected_identities"])} for source,d in result["sources"].items()}))


if __name__=="__main__":main()
