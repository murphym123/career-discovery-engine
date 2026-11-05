"""Publish explicitly experimental, offline triage beside an untouched source audit."""
import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from career_engine import triage as engine
from tools.rank_jobs import load_records, write_outputs
from career_engine.review_quarantine import partition_for_review

WARNING = 'EXPERIMENTAL — NOT INDEPENDENTLY VALIDATED'


def publish(input_path, output_dir, top=15, detailed_budget=200, seed=0, as_of=None):
    input_path,output_dir=Path(input_path),Path(output_dir)
    output_dir.mkdir(parents=True,exist_ok=True)
    status={'experimental':True,'validation_status':'not_independently_validated',
            'warning':WARNING,'input_name':input_path.name,'status':'failed',
            'network_calls':0,'model_calls':0}
    try:
        original=input_path.read_bytes()
        data=json.loads(original)
        records=load_records(input_path)
        if not isinstance(data,dict) or not isinstance(data.get('stats'),dict) or any(not isinstance(c,dict) for c in data['stats'].values()):
            raise ValueError('Expected the unranked source-audit JSON with records and source stats')
        status.update(input_sha256=hashlib.sha256(original).hexdigest(),source_capture_at=data.get('captured_at',''),
                      ranker_version=engine.VERSION,
                      ranker_sha256=hashlib.sha256(Path(engine.__file__).read_bytes()).hexdigest())
        partial=any(counts.get('errors') or counts.get('status') in ('failed','partial_failure')
                    for counts in data['stats'].values())
        available,withheld=partition_for_review(records)
        result=engine.triage(available,top,detailed_budget,10,seed,as_of)
        used={r['identity'] for r in result['audit']}
        for row in withheld:
            key=engine.identity(row);n=1
            while key in used:key=engine.identity(row)+f'#withheld{n}';n+=1
            used.add(key)
            result['audit'].append({**{k:row.get(k,'') for k in ('title','company','location','url','source','source_id','index_url','discovery_urls')},
                'identity':key,'decision':'withheld_for_independent_review','inspection_route':'independent_review_quarantine',
                'review_priority':None,'evidence_confidence':'not_evaluated','qualifications':{'status':'not_evaluated'},
                'supported_experience':[],'competitive_advantage':[],'uncertainty':['Reserved for independent review; no model evaluation.'],
                'verification':{'employer_active_verified':False},'possible_duplicate':None})
        if withheld:
            result['counts']['received']+=len(withheld);result['counts']['retained']+=len(withheld)
            result['counts']['descriptions_available']+=sum(bool(r.get('description')) for r in withheld)
            result['counts']['decisions']['withheld_for_independent_review']=len(withheld)
            for r in withheld:
                source=r.get('source','');result['counts']['sources'][source]=result['counts']['sources'].get(source,0)+1
        result['independent_review_withheld']=len(withheld)
        result.update(experimental=True,validation_status=status['validation_status'],
                      warning=WARNING,unranked_audit={'file':input_path.name,'sha256':status['input_sha256'],
                                                    'source_capture_at':data.get('captured_at',''),
                                                    'partial_source_data':partial})
        brief=output_dir/'experimental-ranked-brief.md'
        write_outputs(result,brief)
        intro=f'# {WARNING}\n\nThe full unranked `{input_path.name}` remains the source of truth. '
        intro+='These are review priorities, not confirmed qualifications or active-vacancy verification. '
        if partial:intro+='**Source retrieval reported errors; this ranking uses partial data.** '
        intro+='No extra source collection or paid/model calls were made to rank these records.\n\n'
        brief.write_text(intro+brief.read_text(),encoding='utf-8')
        byid={r['identity']:r for r in result['audit']}
        investigate=set(result['investigation_queue']);explore=set(result['exploration_queue'])
        review=[{**byid[k],'investigation':k in investigate,'exploration':k in explore}
                for k in dict.fromkeys(result['investigation_queue']+result['exploration_queue'])]
        (output_dir/'experimental-review-queue.json').write_text(json.dumps({
            'experimental':True,'validation_status':status['validation_status'],'warning':WARNING,
            'note':'Complete investigation queue plus bounded score-independent exploration; not recommendations.',
            'records':review},indent=2,ensure_ascii=False)+'\n')
        fields=['identity','title','company','location','url','source','decision','inspection_route',
                'investigation','exploration','experimental','validation_status']
        with (output_dir/'experimental-review-queue.csv').open('w',newline='',encoding='utf-8') as f:
            writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader()
            writer.writerows({**r,'experimental':True,'validation_status':status['validation_status']} for r in review)
        if input_path.read_bytes()!=original:raise RuntimeError('Unranked input changed during publication')
        status.update(status='completed_with_partial_sources' if partial else 'completed',
                      input_preserved=True,counts=result['counts'])
        (output_dir/'README.md').write_text(intro+'Files: experimental-ranked-brief.md/.json, '
            'experimental-ranked-brief-audit.csv (all input rows), experimental-ranked-brief-shortlist.csv, '
            'experimental-review-queue.json/.csv, and run-status.json.\n',encoding='utf-8')
    except Exception as exc:
        # Preserve diagnostics even when ranking fails; workflow always uploads
        # the original unranked artifacts. Do not conceal failure behind success.
        status.update(error_type=type(exc).__name__,input_preserved=(input_path.read_bytes()==original) if input_path.exists() and 'original' in locals() else None)
        raise
    finally:
        (output_dir/'run-status.json').write_text(json.dumps(status,indent=2)+'\n',encoding='utf-8')
    return status


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',required=True)
    p.add_argument('--output-dir',default='output/experimental-ranking')
    p.add_argument('--top',type=int,default=15)
    p.add_argument('--detailed-budget',type=int,default=200)
    p.add_argument('--seed',type=int,default=0)
    p.add_argument('--as-of')
    args=p.parse_args(argv)
    if not 1<=args.top<=30 or not 0<=args.detailed_budget<=1000:p.error('Invalid ranking budget')
    try:
        as_of=datetime.fromisoformat(args.as_of.replace('Z','+00:00')) if args.as_of else datetime.now(timezone.utc)
        if as_of.tzinfo is None:as_of=as_of.replace(tzinfo=timezone.utc)
        status=publish(args.input,args.output_dir,args.top,args.detailed_budget,args.seed,as_of)
    except (OSError,ValueError,RuntimeError,TypeError,KeyError):
        print(json.dumps({'experimental':True,'status':'failed','note':'See run-status.json; unranked audit remains available.'}))
        return 1
    print(json.dumps(status));return 0


if __name__=='__main__':raise SystemExit(main())
