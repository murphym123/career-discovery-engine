"""Manual bounded employer-frame discovery; public evidence, no AI ranking."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

from career_engine.ats import PublicTransport
from career_engine.ats_registry import empty_registry
from career_engine.employer_frame import load_frame
from career_engine.employer_pipeline import PipelineTransport,run_pipeline
from career_engine.registry_state import read_registry,save_registry,atomic_json
from career_engine.publication_audit import build_ledger,write_ledger
from tools.expand_ats import write_capture


def run(frame_path,registry_path,output_dir,base_path=None,discovery_requests=120,collection_requests=60,
        board_cap=15,detail_budget=2,discovery_transport=None,collection_transport=None):
    frame=load_frame(frame_path);registry_path=Path(registry_path);output_dir=Path(output_dir)
    if base_path and Path(base_path).resolve() in (registry_path.resolve(),(output_dir/'employer-source-audit.json').resolve(),(output_dir/'combined-audit.json').resolve()):raise ValueError('Input/output paths overlap')
    if not 1<=discovery_requests<=180 or not 1<=collection_requests<=180 or discovery_requests+collection_requests>180:raise ValueError('Combined request budget must be at most 180')
    if len(frame['rows'])>40:raise ValueError('Select a maximum 40-employer batch first')
    registry=read_registry(registry_path) if registry_path.exists() else empty_registry()
    original=Path(base_path).read_bytes() if base_path else None
    discovery=discovery_transport or PipelineTransport(max_requests=discovery_requests,max_bytes=32_000_000,max_seconds=300)
    collection=collection_transport or (lambda:PublicTransport(max_requests=collection_requests,max_bytes=32_000_000,max_seconds=300))
    data=run_pipeline(frame,registry,discovery,collection,board_cap=board_cap,detail_budget=detail_budget)
    data['frame_input_sha256']=frame['input_sha256']
    data['budgets']={'employers':len(frame['rows']),'pages_per_employer':3,'discovery_requests':discovery_requests,
                     'collection_requests':collection_requests,'boards':board_cap,'details_per_board':detail_budget,
                     'bytes_each_stage':32_000_000,'seconds_each_stage':300}
    write_capture(output_dir/'employer-source-audit.json',data)
    # Separate unknown/research export: proposed URLs are leads, never verified
    # sources. No paid model integration, browser automation or hidden APIs.
    atomic_json(output_dir/'review-queue.json',{'cases':data['review_queue'],'optional_research_contract':{
        'scope':'Only unresolved cases in this file; lawful public evidence, no accounts, private endpoints or paid services',
        'proposal_fields':['frame_id','proposed_url','evidence_url','relationship_note'],
        'verification':'A proposal is untrusted. Corroborate the named entity and observed official hiring link using deterministic public checks before registry insertion.'}})
    channels=[{'frame_id':e['frame_entity']['frame_id'],'legal_name':e['frame_entity']['legal_name'],**c} for e in data['employers'] for c in e['channels']]
    atomic_json(output_dir/'channel-leads.json',{'channels':channels})
    for filename,rows in [('employers.csv',data['employers']),('channel-leads.csv',channels),('review-queue.csv',data['review_queue'])]:
        columns=sorted({k for r in rows for k in r}) or ['frame_id','status']
        with (output_dir/filename).open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=columns);w.writeheader()
            for row in rows:w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()})
    base=json.loads(original) if original else {'records':[],'stats':{}}
    ats={'records':data['records'],'captured_at':data['captured_at'],'discovery':{'boards':data['inventories']}}
    write_ledger(output_dir/'coverage-audit.json',build_ledger(base,ats,registry=registry))
    if original:
        if not isinstance(base.get('records'),list) or not isinstance(base.get('stats'),dict):raise ValueError('Expected existing unranked capture')
        combined={**base,'schema_version':'job-collection/v1','kind':'FULL UNRANKED EXISTING + EMPLOYER FRAME AUDIT; DUPLICATE SIGHTINGS RETAINED',
                  'records':base['records']+data['records'],'stats':{**base['stats'],**data['stats']},
                  'original_source_sha256':hashlib.sha256(original).hexdigest()}
        write_capture(output_dir/'combined-audit.json',combined)
    lines=['# Employer-first discovery — unranked','',data['limits'],'','```json',json.dumps(data['metrics'],indent=2),'```','',
           '| Frame legal entity | Official site | Prior registry state | Channels | Review cases |','|---|---|---|---:|---:|']
    cell=lambda x:str(x).replace('|','\\|').replace('\n',' ')
    for e in data['employers']:
        lines.append(f"| {cell(e['frame_entity']['legal_name'])} | {e.get('official_site',{}).get('status','')} | {e.get('discovery_state_before','')} | {len(e['channels'])} | {len(e['review_queue'])} |")
    (output_dir/'employer-source-audit.md').write_text('\n'.join(lines)+'\n')
    if original and Path(base_path).read_bytes()!=original:raise RuntimeError('Original capture changed')
    save_registry(registry_path,registry)
    return data


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--frame',required=True);p.add_argument('--registry',default='data/ats-registry.json')
    p.add_argument('--output-dir',default='output/employer-discovery');p.add_argument('--base',help='Optional existing unranked capture; never modified')
    p.add_argument('--discovery-requests',type=int,default=120);p.add_argument('--collection-requests',type=int,default=60)
    p.add_argument('--board-cap',type=int,default=15);p.add_argument('--detail-budget',type=int,default=2)
    a=p.parse_args(argv)
    try:data=run(a.frame,a.registry,a.output_dir,a.base,a.discovery_requests,a.collection_requests,a.board_cap,a.detail_budget)
    except (OSError,ValueError,RuntimeError,KeyError) as exc:
        print(json.dumps({'status':'failed','error_type':type(exc).__name__,'registry':'not_reset'}));return 1
    print(json.dumps(data['metrics']));return 0


if __name__=='__main__':raise SystemExit(main())
