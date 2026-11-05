"""Manually expand an existing DC audit via public ATS boards; no paid search."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
from datetime import datetime,timezone,timedelta
from urllib.parse import urlsplit

from career_engine.ats import PublicTransport,retrieve_board,stamp,enrich_regional_metadata
from career_engine.ats_registry import empty_registry,validate_registry,source_seeds,discover_commoncrawl,register,select_boards
from career_engine.ats_coverage import assess_increment
from career_engine.extract import safe_public_url


def write_capture(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    columns=['identity','source','source_id','board_key','title','company','company_evidence','location','geography',
             'url','posted_at','updated_at','closing_at','retrieved_at','record_kind','evidence_status','detail_status',
             'duplicate_status','description','description_truncated','early_career_signal','function_signals']
    with path.with_suffix('.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=columns);w.writeheader()
        for r in data['records']:
            w.writerow({k:json.dumps(r.get(k),ensure_ascii=False) if isinstance(r.get(k),(list,dict)) else r.get(k,'') for k in columns})


def expand(base_path,registry_path,output_dir,combined_path,board_budget=30,seed_queries=6,imports=None,transport=None,extra_details=20):
    base_path,registry_path,output_dir,combined_path=map(Path,(base_path,registry_path,output_dir,combined_path))
    output_dir.mkdir(parents=True,exist_ok=True)
    original=base_path.read_bytes();base=json.loads(original)
    if not isinstance(base.get('records'),list) or not isinstance(base.get('stats'),dict):raise ValueError('Expected an unranked source audit')
    registry=validate_registry(json.loads(registry_path.read_text())) if registry_path.exists() else empty_registry()
    transport=transport or PublicTransport(max_requests=min(300,board_budget*6+seed_queries*4+10))
    for host,until in registry.get('host_not_before',{}).items():
        try:
            if datetime.fromisoformat(until)>datetime.now(timezone.utc):transport.blocked.add(host)
        except (ValueError,TypeError):pass
    before=set(registry['boards']);source_added=source_seeds(registry,base['records'])
    if imports:
        for entry in imports:
            if not isinstance(entry,dict) or not isinstance(entry.get('url'),str) or not safe_public_url(str(entry.get('evidence_url',''))):continue
            register(registry,entry['url'],{'kind':'explicit_public_URL_evidence_import','url':entry['evidence_url'],
                     'query_family':entry.get('query_family','unrestricted'),'note':'Imported URL evidence, not an employer name allowlist; live provider response still required.'})
    seed_log=discover_commoncrawl(transport,registry,seed_queries)
    selected=select_boards(registry,board_budget)
    records=[];board_stats=[]
    for entry in selected:
        rows,stats=retrieve_board(transport,entry)
        entry.update({k:stats[k] for k in ('status','dc_dmv','unique','descriptions')})
        entry['last_checked_run']=registry['run'];entry['last_checked_at']=stamp();entry['last_errors']=stats['errors']
        for row in rows:row['board_discovery_evidence']=entry['evidence']
        records+=rows;board_stats.append(stats)
        if len(records)>=20000:
            board_stats[-1]['errors'].append('run_record_cap');break
        if any('budget' in str(x) or 'byte_limit' in str(x) for x in stats['errors']):break
    extra_log=enrich_regional_metadata(transport,records,registry['boards'],extra_details,registry['run'])
    for stats in board_stats:
        related=[x for x in extra_log if x['board_key']==stats['board_key']]
        stats['extra_detail_requests']=sum(x['requests'] for x in related);stats['requests']+=stats['extra_detail_requests']
        stats['descriptions']=sum(bool(r['description']) for r in records if r['board_key']==stats['board_key'])
        stats['errors'] += ['extra_detail_'+x['status'] for x in related if x['status']!='ok']
        if stats['errors'] and stats['unique']:stats['status']='partial_failure'
        registry['boards'][stats['board_key']]['descriptions']=stats['descriptions']
    coverage=assess_increment(base['records'],records)
    status='partial_failure' if any(s['errors'] for s in board_stats) or any(q.get('status')!='ok' for q in seed_log) else 'ok'
    stats={'requests':len(transport.requests),'received':len(records),'unique':len(records),'status':status,
           'errors':(['Some seed queries or boards failed; inspect ATS discovery report'] if status!='ok' else []),
           'capped':len(selected)<len(registry['boards']) or any(s['capped'] for s in board_stats)}
    data={'kind':'UNRANKED PUBLIC ATS DISCOVERY; NOT EMPLOYER-CONFIRMED ACTIVE OR FIT RECOMMENDATIONS',
          'captured_at':stamp(),'records':records,'stats':{'public_ats':stats},'coverage':coverage,
          'discovery':{'registry_boards':len(registry['boards']),'new_registry_boards':len(set(registry['boards'])-before),
                       'source_link_boards_added':source_added,'seed_queries':seed_log,'selected_boards':len(selected),
                       'boards':board_stats,'extra_detail_inspections':extra_log,'unsupported_platform_links':registry['unsupported']},
          'transport':{'requests':transport.requests,'bytes':transport.bytes,'maximum_requests':transport.max_requests},
          'baseline_sha256':hashlib.sha256(original).hexdigest(),
          'limits':'Bounded index samples and boards; wildcard/prefix metadata search is not a full-text DC employment index. Every retrieved row remains auditable; no fit filtering.'}
    write_capture(output_dir/'ats-source-audit.json',data)
    combined={'kind':'FULL UNRANKED FIVE-SOURCE + PUBLIC ATS AUDIT; DUPLICATE SIGHTINGS RETAINED',
              'captured_at':data['captured_at'],'original_five_source_capture_at':base.get('captured_at',''),
              'records':base['records']+records,'stats':{**base['stats'],'public_ats':stats},
              'ats_coverage':coverage,'original_five_source_sha256':data['baseline_sha256']}
    write_capture(combined_path,combined)
    for request in transport.requests:
        if request.get('status') in (403,429):
            wait=86400
            if request.get('status')==429 and str(request.get('retry_after','')).isdigit():wait=max(60,min(int(request['retry_after']),604800))
            registry['host_not_before'][urlsplit(request['url']).hostname]=(datetime.now(timezone.utc)+timedelta(seconds=wait)).isoformat()
    registry_path.parent.mkdir(parents=True,exist_ok=True);registry_path.write_text(json.dumps(registry,indent=2,ensure_ascii=False)+'\n')
    def c(v):return str(v).replace('|','\\|').replace('\n',' ')
    lines=['# Public ATS discovery — unranked','',
           'The original five-source audit is unchanged. ATS publication is observed, not independent confirmation of accepting applications or candidate qualification. No paid search/API or model calls.',
           '', '## Measured coverage','', '```json',json.dumps(coverage,indent=2),'```','',
           f"Registry: {len(registry['boards'])} discovered boards; {len(selected)} selected; {len(transport.requests)} requests; {transport.bytes} response bytes.",'',
           '## Board access','', '| Board | Status | Rows | DC/DMV labels | Descriptions | Errors |','|---|---|---:|---:|---:|---|']
    for s in board_stats:lines.append(f"| {c(s['board_key'])} | {s['status']} | {s['unique']} | {s['dc_dmv']} | {s['descriptions']} | {c(', '.join(s['errors']))} |")
    lines+=['','## New regional records requiring review','',
            'No match to this bounded five-source capture is not proof of unique vacancy, suitability, pay, level or comprehensive discovery. Title/description signals are for review only.','',
            '| Role | Employer label | Original location | Duplicate status | Early-career signal |','|---|---|---|---|---|']
    for r in records:
        if r['geography']=='dc_dmv':lines.append(f"| [{c(r['title'])}]({r['url']}) | {c(r['company'])} | {c(r['location'])} | {r['duplicate_status']} | {r['early_career_signal']['kind']} |")
    lines+=['','## Seed query outcomes','', '```json',json.dumps(seed_log,indent=2),'```','',data['limits'],'']
    (output_dir/'ats-source-audit.md').write_text('\n'.join(lines),encoding='utf-8')
    combined_path.with_suffix('.md').write_text('# Expanded unranked audit\n\nThe full JSON/CSV retains every original and ATS record, including duplicates and uncertain/off-market records. See the original dc-source-audit.md and ats-discovery/ats-source-audit.md.\n',encoding='utf-8')
    if base_path.read_bytes()!=original:raise RuntimeError('Original five-source audit changed')
    return data


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base',default='output/dc-source-audit.json');p.add_argument('--registry',default='data/ats-registry.json')
    p.add_argument('--output-dir',default='output/ats-discovery');p.add_argument('--combined-output',default='output/dc-expanded-audit.json')
    p.add_argument('--board-budget',type=int,default=30);p.add_argument('--seed-queries',type=int,default=6)
    p.add_argument('--extra-details',type=int,default=20,help='Shared regional metadata description budget (0–30), after board discovery')
    p.add_argument('--evidence-import',help='Optional JSON list of public ATS URLs with evidence_url; never company-name guesses')
    args=p.parse_args(argv)
    if not 1<=args.board_budget<=100 or not 0<=args.seed_queries<=20 or not 0<=args.extra_details<=30:p.error('Invalid ATS budget')
    try:
        imports=json.loads(Path(args.evidence_import).read_text()) if args.evidence_import else None
        if imports is not None and not isinstance(imports,list):p.error('Evidence import must be a list')
        data=expand(args.base,args.registry,args.output_dir,args.combined_output,args.board_budget,args.seed_queries,imports,extra_details=args.extra_details)
    except (OSError,ValueError,RuntimeError,KeyError) as e:
        print(json.dumps({'status':'failed','error_type':type(e).__name__,'original_audit':'preserved; see existing files'}));return 1
    print(json.dumps({'coverage':data['coverage'],'discovered_boards':data['discovery']['registry_boards'],
                      'selected_boards':data['discovery']['selected_boards'],'status':data['stats']['public_ats']['status']}))
    return 0 if any(s['status']!='failed' for s in data['discovery']['boards']) else 1


if __name__=='__main__':raise SystemExit(main())
