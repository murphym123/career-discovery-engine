"""Paired diagnostic on fixed real records; labels never enter the evaluator."""
import argparse
import hashlib
import json
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from career_engine.__main__ import clean, score, select
from career_engine.discovery import geography, expired, screening_hold, deduplicate
from career_engine.triage import triage
from tools.rank_jobs import load_records, write_outputs


def baseline(records,top,as_of):
    """Frozen pre-Phase-3 engine functions, preserved for existing callers/tests.

    Same cleaning, scoring, dedup, date/geography/title holds and diversity caps.
    This old path never interprets qualification evidence beyond its crude rules.
    """
    prepared=[]
    for raw in records:
        j=clean(raw)
        if not j:continue
        j['identity']=raw.get('identity') or f"{j['source']}:{j['source_id']}"
        j['score'],j['lane']=score(j);prepared.append(j)
    jobs=deduplicate(prepared)
    eligible=[j for j in jobs if j['score']>=22 and not expired(j,as_of) and not screening_hold(j)
              and geography(j['location']) not in ('elsewhere','remote_eligibility_unknown','regional_worksite_unknown')
              and (j['source']!='brave' or j.get('extraction_status')=='jobposting_metadata')]
    return [j['identity'] for j in select(eligible,top)]


def metrics(ids,labels,partition=None):
    labels={k:v for k,v in labels.items() if partition is None or v['partition']==partition}
    judged=[labels[k] for k in ids if k in labels]
    counts=Counter(x['label'] for x in judged)
    positive=counts['plausible']+counts['stretch']
    known=sum(v for k,v in counts.items() if k!='unknown')
    return {'selected':len(ids),'judged':len(judged),'unjudged':sum(k not in labels for k in ids),
            'labels':dict(counts),'plausible_or_stretch':positive,'judged_nonunknown':known,
            'relevance_proxy_judged_nonunknown':round(positive/known,4) if known else None,
            'conservative_positive_fraction_of_all_selected':round(positive/len(ids),4) if ids else None,
            'note':'Plausible/stretch analyst review is a relevance proxy, not verified qualification, market precision or hiring probability. Unknown/unjudged are not silently counted as negative.'}


def compare(records,labels,as_of):
    labels={r['identity']:r for r in labels}
    new=triage(records,30,200,20,17,as_of)
    old15=baseline(records,15,as_of);old30=baseline(records,30,as_of)
    # Apply the same soft diversity selector at each requested length for v1.
    new15=triage(records,15,200,20,17,as_of)['shortlist'];new30=new['shortlist']
    results={}
    for name,ids in [('old_top15',old15),('new_top15',new15),('old_top30',old30),('new_top30',new30)]:
        results[name]={'identities':ids,'all_review_labels':metrics(ids,labels),
                       'holdout_only':metrics(ids,labels,'holdout')}
    judgments={r['identity']:r for r in new['audit']}
    false_negatives={}
    for part in ('development','holdout'):
        positives=[k for k,v in labels.items() if v['partition']==part and v['label'] in ('plausible','stretch')]
        false_negatives[part]={'labeled_positive_total':len(positives),
            'old_missed_top30':[k for k in positives if k not in old30],
            'new_missed_top30':[{'identity':k,'decision':judgments[k]['decision'],
                                'route':judgments[k]['inspection_route']} for k in positives if k not in new30],
            'new_qualification_gap_false_negatives':[k for k in positives if judgments[k]['decision']=='qualification_gap']}
    return {'evaluation_status':'Audit-informed diagnostic; original 55-case holdout was unsealed for parser-error correction, so final results are not untouched holdout validation.', 'as_of':as_of.isoformat(),'input_count':len(records),
            'description_count':sum(bool(r.get('description')) for r in records),
            'input_representation':'Use input manifest: committed paired benchmark uses analyst abstracts; raw-capture diagnostic uses original descriptions.',
            'baseline':'Main at 078c1fc165972c81589e6fc3f5408fa29e2c9111: score/select/screening_hold/dedup unchanged; threshold22; given date.',
            'labels':dict(Counter(r['partition'] for r in labels.values())),
            'results':results,'false_negatives':false_negatives,
            'all_holdout_decisions':[{'identity':k,'label':v['label'],'new_decision':judgments[k]['decision'],
                                      'in_old_top30':k in old30,'in_new_top30':k in new30,'rationale':v['rationale']}
                                     for k,v in labels.items() if v['partition']=='holdout'],
            'new_counts':new['counts'],
            'limitations':['Same analyst and source snapshot; not blind or representative.',
                           'Holdout selected before evaluator implementation; development examples informed rules.',
                           'Stratified retrieval/inspection and incomplete labels prevent market recall or whole-corpus precision estimates.',
                           'Top-K relevance proxy includes stretches, not only fully qualified applications.',
                           'Unjudged and unknown records have explicit denominators. No performance inference from synthetic stress data.']},new


def stress(records,sizes=(327,5000,20000)):
    results=[]
    for size in sizes:
        expanded=[]
        for n in range(size):
            r=dict(records[n%len(records)]);r['identity']=f"stress:{n}";expanded.append(r)
        start=time.perf_counter();report=triage(expanded,30,200,20,17)
        results.append({'records':size,'seconds':round(time.perf_counter()-start,4),
                        'detailed_budget':200,'analyzed':report['counts']['detailed_analyses'],
                        'retained':report['counts']['retained'],'cost':report['cost'],
                        'data_kind':'Repeated actual metadata with synthetic unique identities; scalability only, not accuracy.'})
    return results


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',default='docs/issue7/evaluation-input.json')
    p.add_argument('--labels',default='docs/issue7/review-labels.json')
    p.add_argument('--output',default='docs/issue7/comparison.json')
    p.add_argument('--report',default='output/issue7/ranked-brief.md')
    p.add_argument('--as-of',default='2026-11-05T07:58:27+00:00')
    p.add_argument('--stress',action='store_true')
    args=p.parse_args(argv)
    records=load_records(args.input); labels=json.loads(Path(args.labels).read_text())['records']
    comparison,report=compare(records,labels,datetime.fromisoformat(args.as_of))
    comparison['input_sha256']=hashlib.sha256(Path(args.input).read_bytes()).hexdigest()
    comparison['label_sha256']=hashlib.sha256(Path(args.labels).read_bytes()).hexdigest()
    if args.stress:comparison['stress']=stress(records)
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps(comparison,indent=2)+'\n')
    write_outputs(report,args.report)
    print(json.dumps({k:v for k,v in comparison.items() if k in ('input_count','description_count','labels','false_negatives','new_counts','stress')}))
    return 0


if __name__=='__main__':raise SystemExit(main())
