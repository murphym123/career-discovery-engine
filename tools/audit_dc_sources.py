"""Unranked, manual DC-source evidence capture; never runs paid APIs or applies."""
import argparse
from dataclasses import asdict
from datetime import datetime,timezone
import csv
import json
from pathlib import Path

import requests
from career_engine.dc_sources import ALL_DC_SOURCES,retrieve_dc,observe_selected_details
from career_engine.sources import SourceStats
from career_engine.discovery import geography
from career_engine.publication_audit import PublicationObserver,build_ledger,write_ledger
from career_engine.collection_health import collection_health,health_markdown


def capture(sources=ALL_DC_SOURCES,max_details=20,session=None,strategy="diverse",seed=0):
    records,stats=[],{}
    session=PublicationObserver(session if session is not None else requests.Session())
    for source in sources:
        counts=SourceStats()
        try:
            records.extend(retrieve_dc(session,source,max_details,counts,strategy=strategy,seed=seed))
        except Exception as exc:
            # Isolate an adapter defect as well as ordinary access failures.
            # Preserve already-yielded rows; never log arbitrary exception text.
            counts.unique=sum(r.get('source')==source for r in records)
            counts.errors.append('source_adapter_exception_'+type(exc).__name__)
            counts.status='partial_failure' if counts.unique else 'failed'
        stats[source]=asdict(counts)
        stats[source].pop("recommended",None)
    # No scoring or selection. Raw location and description remain inspectable.
    for row in records:
        row["geography"]=geography(row["location"])
    return dict(kind="ACTUAL RETRIEVED BOARD RECORDS; NOT CONFIRMED OPENINGS OR RECOMMENDATIONS",
                captured_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                inspection_strategy=strategy,inspection_seed=seed,records=records,stats=stats,publication_observations=session.observations)


def collection_exit_code(data,allow_partial=False):
    health=collection_health(data['stats'])
    errors=health['state']!='ok' or bool(data.get('selected_detail_check',{}).get('stats',{}).get('errors'))
    if not errors:return 0
    return 0 if allow_partial and data.get('records') and health['state']!='failed' else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources",default=",".join(ALL_DC_SOURCES))
    parser.add_argument("--inspection-strategy",choices=("diverse","prefix"),default="diverse")
    parser.add_argument("--inspection-seed",type=int,default=0)
    parser.add_argument("--max-details",type=int,default=20)
    parser.add_argument("--output",type=Path,default=Path("output/dc-source-audit.json"))
    parser.add_argument("--allow-partial",action="store_true",help="Retain explicit partial status but exit successfully if some source records survived. All-source failure still exits 1.")
    parser.add_argument("--from-capture",type=Path,help="Enrich an existing actual capture without requesting its indexes again")
    parser.add_argument("--detail-ids",default="",help="Explicit source:id list for a manually stratified requirements check")
    args=parser.parse_args()
    sources=list(dict.fromkeys(args.sources.split(",")))
    if any(source not in ALL_DC_SOURCES for source in sources) or not 0<=args.max_details<=30:
        parser.error("Unsupported DC source or detail budget (0–30)")
    if args.from_capture:
        data=json.loads(args.from_capture.read_text())
        identities=list(dict.fromkeys(filter(None,args.detail_ids.split(","))))
        if not identities:
            parser.error("--from-capture requires explicit --detail-ids")
        stats=observe_selected_details(requests.Session(),data["records"],identities,args.max_details)
        data["selected_detail_check"]={"identities":identities,"stats":asdict(stats),"checked_at":datetime.now(timezone.utc).isoformat(timespec="seconds")}
        for source,counts in data["stats"].items():
            counts["unique"]=sum(row["source"]==source for row in data["records"])
            counts.pop("recommended",None)
    else:
        if args.detail_ids:
            parser.error("--detail-ids requires --from-capture")
        data=capture(sources,args.max_details,strategy=args.inspection_strategy,seed=args.inspection_seed)
    data['collection_health']=collection_health(data['stats'])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(data,indent=2,ensure_ascii=False)+"\n")
    write_ledger(args.output.parent/'coverage-audit.json',build_ledger(data))
    def cell(value):return str(value).replace("|","\\|").replace("\n"," ")
    lines=["# DC source audit — unranked", "",health_markdown(data['collection_health']),"", data["kind"], "",
           "These are observed board records, not employer-confirmed openings or recommendations. Full public descriptions, where retrieved, are in the CSV.", "",
           "| Source | Records | DC/DMV labels | Descriptions read | Index only | Status |", "|---|---:|---:|---:|---:|---|"]
    for source,counts in data["stats"].items():
        source_rows=[row for row in data["records"] if row["source"]==source]
        observed=sum(bool(row.get("description")) for row in source_rows)
        lines.append(f'| {source} | {len(source_rows)} | {sum(row.get("geography")=="dc_dmv" for row in source_rows)} | {observed} | {len(source_rows)-observed} | {counts.get("status", "")} |')
    lines += ['', '### Access and index observations', '', '```json',json.dumps({s:{k:c.get(k,[]) for k in ('index_attempts','access_observations')} for s,c in data['stats'].items()}),'```','']
    lines += ["", "All records remain below; location labels and inspection signals are not findings of suitability.", "",
              "| Source | Role | Employer | Raw location | Detail observation | Inspection reason |", "|---|---|---|---|---|---|"]
    reasons={"latest_regional_listing":"Latest regional listing","plausible_early_career":"Early-career signal",
             "employer_function_diversity":"Employer/function variety","title_exploration":"Title variety",
             "low_signal_random_review":"Exploratory review","budget_fill":"Remaining budget",
             "not_selected_budget":"Not inspected (budget)","source_prefix":"Source-order comparison"}
    for row in data["records"]:
        lines.append(f'| {cell(row["source"])} | [{cell(row["title"])}]({row["url"]}) | {cell(row["company"])} | {cell(row["location"])} | {cell(row["detail_status"])} | {reasons.get(row.get("inspection_stratum"), "Selected manually / not recorded")} |')
    args.output.with_suffix(".md").write_text("\n".join(lines)+"\n")
    with args.output.with_suffix(".csv").open("w",newline="",encoding="utf-8") as f:
        columns=("source","source_id","title","company","location","geography","url","index_url","discovery_urls","posted_at","posted_at_raw","retrieved_at","evidence_status","detail_status","record_kind","inspection_stratum","experience_hint","category","employment_type","description")
        writer=csv.DictWriter(f,fieldnames=columns,extrasaction="ignore");writer.writeheader();writer.writerows(data["records"])
    print(json.dumps({"records":len(data["records"]),"collection_health":data['collection_health'],"stats":data["stats"],"output":str(args.output)}))
    return collection_exit_code(data,args.allow_partial)


if __name__=="__main__":
    raise SystemExit(main())
