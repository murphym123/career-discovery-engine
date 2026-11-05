"""Offline evidence triage for any source's JSON/CSV listings; zero API calls."""
import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from career_engine.triage import triage


def load_records(path):
    path=Path(path)
    if path.suffix.lower()=='.csv':
        with path.open(encoding='utf-8-sig',newline='') as f: return list(csv.DictReader(f))
    value=json.loads(path.read_text(encoding='utf-8'))
    rows=value.get('records') if isinstance(value,dict) else value
    if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):
        raise ValueError('Expected a JSON list of job objects, a records array, or CSV records')
    return rows


def _cell(value):
    return str(value or '').replace('|','\\|').replace('\n',' ').replace('\r',' ')


def write_outputs(result,output):
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    byid={r['identity']:r for r in result['audit']}
    lines=['# Evidence-backed career triage','',
           f"As of {result['as_of']} · {result['counts']['retained']} records retained · {result['counts']['detailed_analyses']} descriptions analyzed.",'',
           'Review priorities and analyst evidence do not establish hiring odds, complete qualification, or current availability. No posting in this output is independently employer-verified active.',
           'Diagnostic triage: recommendation quality has not been validated on a fresh independent holdout.',
           'Candidate capabilities are user-reported. Competitive advantages are hypotheses, not comparisons with an observed applicant pool.','',
           '## Shortlist — reasonable applications and explicit stretches','']
    for n,key in enumerate(result['shortlist'],1):
        r=byid[key]; q=r['qualifications']
        lines += [f"### {n}. {_cell(r['title'])} — {_cell(r['company'])}",'',
                  f"[{_cell(r['title'])}]({r['url']}) · {_cell(r['location'])} · **{r['decision']}**",'',
                  f"- Career relevance: {', '.join(r['career_relevance']['families']) or 'uncertain adjacency'}.",
                  '- Supported experience: '+('; '.join(e['capability']+': '+e['candidate_evidence'] for e in r['supported_experience']) or 'Not enough evidence.'),
                  '- Posting evidence for transfer: '+('; '.join(e['posting_evidence'] for e in r['supported_experience'][:3]) or 'Unavailable.'),
                  '- Qualifications: '+('; '.join(dict.fromkeys(x['text'] for x in q['requirements'])) or 'Not established.'),
                  '- Potential advantage: '+('; '.join(x['hypothesis']+' '+x['confidence'] for x in r['competitive_advantage']) or 'No evidence-supported comparative advantage established.'),
                  '- Gaps/verification: '+('; '.join(x['evidence']+' — '+x['assessment'] for x in q['mandatory_gaps']+q['risks']) or 'Verify exact experience duration, portfolio, eligibility and full requirements.'),
                  '- Preferences/trainable: '+('; '.join(dict.fromkeys(x['text'] for x in q['preferences_or_trainable'])) or 'None extracted; parsing may miss preferences.'),
                  f"- Evidence confidence: {r['evidence_confidence']}; {r['description_representation']}.",
                  '- Status/freshness: '+json.dumps(r['verification'],ensure_ascii=False),
                  '- Terms: '+('; '.join(f'{k}={v}' for k,v in r['terms'].items() if v) or 'Not supplied in structured fields; check original posting.'),
                  '- Uncertainty: '+'; '.join(r['uncertainty']),
                  f"- Provenance: source={r['source']}; sightings={r['sources']}; identity={r['identity']}.",'']
    if not result['shortlist']:lines+=['No record cleared recommendation/stretch triage. Review investigation and exploration queues; this is not evidence that no good jobs exist.','']
    lines+=['## Exploration / uncertainty review — not recommendations','',
            '| Role | Employer | Decision | Reason |','|---|---|---|---|']
    for key in result['exploration_queue']:
        r=byid[key]
        lines.append(f"| [{_cell(r['title'])}]({r['url']}) | {_cell(r['company'])} | {r['decision']} | {_cell(r['inspection_route'])}; {_cell(r['qualifications']['status'])} |")
    lines+=['','## Coverage and retention','',
            'Every input row, including duplicates, invalid rows, qualification gaps, closed-by-date and uncertain records, remains in the JSON/CSV audit. Investigation queue is complete in JSON; the exploration table is a bounded sample.',
            '', '```json',json.dumps(result['counts'],indent=2),'```','',
            'No network, model calls, applications or scheduled work. Exact employer/title overlaps are flagged, never merged by title.','']
    output.write_text('\n'.join(lines),encoding='utf-8')
    output.with_suffix('.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    fields=['identity','decision','title','company','location','url','source','sources','review_priority','geography',
            'evidence_confidence','inspection_route','verification','terms','career_relevance','qualifications',
            'supported_experience','competitive_advantage','uncertainty','possible_duplicate','source_id','index_url','discovery_urls','source_evidence']
    if result.get('experimental'): fields+=['experimental','validation_status']
    for path,rows in [(output.with_name(output.stem+'-audit.csv'),result['audit']),
                      (output.with_name(output.stem+'-shortlist.csv'),[byid[k] for k in result['shortlist']])]:
        with path.open('w',encoding='utf-8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
            for r in rows:
                row={}
                for key in fields:
                    value=result.get(key,r.get(key,'')) if key in ('experimental','validation_status') else r.get(key,'')
                    row[key]=json.dumps(value,ensure_ascii=False) if isinstance(value,(dict,list)) else value
                w.writerow(row)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',required=True,help='JSON list, source-audit records JSON, or CSV from any adapter')
    p.add_argument('--output',default='output/ranked-brief.md')
    p.add_argument('--top',type=int,default=15)
    p.add_argument('--detailed-budget',type=int,default=200)
    p.add_argument('--exploration-slots',type=int,default=10)
    p.add_argument('--seed',type=int,default=0)
    p.add_argument('--as-of',help='ISO time for reproducible date screening; default UTC now')
    args=p.parse_args(argv)
    if args.top<1 or args.detailed_budget<0 or args.exploration_slots<0:p.error('invalid triage budget')
    try:
        now=datetime.fromisoformat(args.as_of.replace('Z','+00:00')) if args.as_of else datetime.now(timezone.utc)
        if now.tzinfo is None:now=now.replace(tzinfo=timezone.utc)
        result=triage(load_records(args.input),args.top,args.detailed_budget,args.exploration_slots,args.seed,now)
    except (ValueError,OSError) as e:p.error(str(e))
    write_outputs(result,args.output)
    print(json.dumps({'output':args.output,'counts':result['counts'],'cost':result['cost']}))
    return 0


if __name__=='__main__':raise SystemExit(main())
