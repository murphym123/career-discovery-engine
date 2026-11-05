"""Unsealed diagnostic comparison only. Refuses records committed to blind review."""
import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from career_engine.triage import triage
from tools.benchmark_triage import baseline, metrics
from tools.rank_jobs import load_records, write_outputs
from tools.prepare_blind_review import normalized_pair


def assert_not_sealed(records,seals):
    sealed={r['url_sha256'] for s in seals for r in s['records']}
    pairs={r.get('employer_title_sha256') for s in seals for r in s['records']}
    pairs.discard(None)
    if any(hashlib.sha256(str(r.get('url','')).rstrip('/').encode()).hexdigest() in sealed or hashlib.sha256('|'.join(normalized_pair(r)).encode()).hexdigest() in pairs for r in records):
        raise ValueError('Refusing to evaluate sealed review records; independent review is pending')


def diagnose(records,labels,seals,as_of,seed=9):
    assert_not_sealed(records,seals)
    judgments={r['identity']:r for r in labels}
    new=triage(records,30,200,10,seed,as_of)
    results={}
    for k in (15,30):
        old_ids=baseline(records,k,as_of)
        new_ids=triage(records,k,200,10,seed,as_of)['shortlist']
        results[f'old_top{k}']={'requested_top':k,'identities':old_ids,**metrics(old_ids,judgments)}
        results[f'new_top{k}']={'requested_top':k,'identities':new_ids,**metrics(new_ids,judgments)}
    positives=[i for i,r in judgments.items() if r['label'] in ('plausible','stretch')]
    rejected=[r for r in new['audit'] if r['identity'] not in set(new['shortlist'])]
    rejected.sort(key=lambda r:hashlib.sha256(f"rejected|{seed}|{r['url']}".encode()).hexdigest())
    def reviewed(row):
        lab=judgments.get(row['identity'],{})
        return {k:row[k] for k in ('identity','title','company','location','url','decision')}|{'analyst_label':lab.get('label','unreviewed'),'rationale':lab.get('rationale','')}
    output={'kind':'DIAGNOSTIC ONLY — NOT INDEPENDENT VALIDATION','independent_review_available':False,
        'validation_status':'pending_independent_review','model_rules_changed':False,
        'as_of':as_of.isoformat(),'input_count':len(records),'label_counts':dict(Counter(r['label'] for r in labels)),
        'results':results,'false_negatives':{'labeled_positive_total':len(positives),
            'old_outside_top30':[i for i in positives if i not in results['old_top30']['identities']],
            'new_outside_top30':[i for i in positives if i not in results['new_top30']['identities']]},
        'suspicious_new_top30':[reviewed(r) for r in new['audit'] if r['identity'] in new['shortlist'] and judgments.get(r['identity'],{}).get('label') in ('ineligible','unlikely','unknown')],
        'random_not_shortlisted_sample':[reviewed(r) for r in rejected[:10]],
        'all_diagnostic_decisions':[reviewed(r) for r in new['audit']],
        'counts':new['counts'],'cost':new['cost'],
        'limitations':['Labels by model author, not independent review; source and employer sampling nonrepresentative.',
                      'No blind-packet record was passed to the ranker. No rule changes informed by these new examples.',
                      'Published ATS record observed, not independently verified accepting applications; updated_at is not posting date.',
                      'Requested top 30 may contain fewer than 30: no padding with rejected or unknown roles.',
                      'No qualified human preference between ranked and unranked output has been measured.']}
    return output,new


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',required=True);p.add_argument('--labels',required=True)
    p.add_argument('--seal',action='append',required=True);p.add_argument('--as-of',required=True)
    p.add_argument('--output',required=True);p.add_argument('--report',required=True)
    args=p.parse_args(argv)
    try:
        records=load_records(args.input);labels=json.loads(Path(args.labels).read_text())['records']
        seals=[json.loads(Path(s).read_text()) for s in args.seal]
        result,ranking=diagnose(records,labels,seals,datetime.fromisoformat(args.as_of))
    except (OSError,ValueError,KeyError) as e:p.error(str(e))
    result['input_sha256']=hashlib.sha256(Path(args.input).read_bytes()).hexdigest()
    result['labels_sha256']=hashlib.sha256(Path(args.labels).read_bytes()).hexdigest()
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps(result,indent=2)+'\n')
    write_outputs(ranking,args.report)
    print(json.dumps({'input':len(records),'validation_status':result['validation_status'],
                      'results':{k:{x:v[x] for x in ('selected','labels','plausible_or_stretch','judged_nonunknown')} for k,v in result['results'].items()},
                      'false_negatives':result['false_negatives']}))
    return 0


if __name__=='__main__':raise SystemExit(main())
