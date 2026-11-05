"""Unranked, manual DC-source evidence capture; never runs paid APIs or applies."""
import argparse
from dataclasses import asdict
from datetime import datetime,timezone
import csv
import json
from pathlib import Path

import requests
from career_engine.dc_sources import DC_SOURCES,retrieve_dc,observe_selected_details
from career_engine.sources import SourceStats
from career_engine.discovery import geography


def capture(sources=DC_SOURCES,max_details=20,session=None):
    records,stats=[],{}
    session=session if session is not None else requests.Session()
    for source in sources:
        counts=SourceStats()
        records.extend(retrieve_dc(session,source,max_details,counts))
        stats[source]=asdict(counts)
        stats[source].pop("recommended",None)
    # No scoring or selection. Raw location and description remain inspectable.
    for row in records:
        row["geography"]=geography(row["location"])
    return dict(kind="ACTUAL RETRIEVED BOARD RECORDS; NOT CONFIRMED OPENINGS OR RECOMMENDATIONS",
                captured_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                records=records,stats=stats)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources",default="senate,unionjobs")
    parser.add_argument("--max-details",type=int,default=20)
    parser.add_argument("--output",type=Path,default=Path("output/dc-source-audit.json"))
    parser.add_argument("--from-capture",type=Path,help="Enrich an existing actual capture without requesting its indexes again")
    parser.add_argument("--detail-ids",default="",help="Explicit source:id list for a manually stratified requirements check")
    args=parser.parse_args()
    sources=list(dict.fromkeys(args.sources.split(",")))
    if any(source not in DC_SOURCES for source in sources) or not 0<=args.max_details<=30:
        parser.error("Only senate/unionjobs and a 0–30 detail budget are allowed")
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
        data=capture(sources,args.max_details)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(data,indent=2,ensure_ascii=False)+"\n")
    def cell(value):return str(value).replace("|","\\|").replace("\n"," ")
    lines=["# DC source audit — unranked", "", data["kind"], "",
           "These are observed board records, not employer-confirmed openings or recommendations. Full public descriptions, where retrieved, are in the CSV.", "",
           "| Source | Role | Employer | Raw location | Detail observation |", "|---|---|---|---|---|"]
    for row in data["records"]:
        lines.append(f'| {cell(row["source"])} | [{cell(row["title"])}]({row["url"]}) | {cell(row["company"])} | {cell(row["location"])} | {cell(row["detail_status"])} |')
    args.output.with_suffix(".md").write_text("\n".join(lines)+"\n")
    with args.output.with_suffix(".csv").open("w",newline="",encoding="utf-8") as f:
        columns=("source","source_id","title","company","location","geography","url","index_url","discovery_urls","posted_at","retrieved_at","evidence_status","detail_status","record_kind","description")
        writer=csv.DictWriter(f,fieldnames=columns,extrasaction="ignore");writer.writeheader();writer.writerows(data["records"])
    print(json.dumps({"records":len(data["records"]),"stats":data["stats"],"output":str(args.output)}))
    return 1 if any(counts.get("errors") for counts in data["stats"].values()) or data.get("selected_detail_check",{}).get("stats",{}).get("errors") else 0


if __name__=="__main__":
    raise SystemExit(main())
