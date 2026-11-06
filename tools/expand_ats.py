"""Manually expand an existing DC audit via public ATS boards; no paid search."""
import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from datetime import datetime,timezone,timedelta
from urllib.parse import urlsplit

from career_engine.ats import PublicTransport,retrieve_board,stamp,enrich_regional_metadata
from career_engine.ats_registry import empty_registry,source_seeds,discover_commoncrawl,register,select_boards
from career_engine.ats_coverage import assess_increment
from career_engine.extract import safe_public_url
from career_engine.employer_discovery import EmployerTransport,discover_directory,discover_employer_links
from career_engine.public_sources import retrieve_impact,discover_jazz_sitemap
from career_engine.regional_selection import select_regional_boards,mark_seen
from career_engine.registry_state import read_registry,save_registry
from career_engine.ats import FetchError
from career_engine.markets import DC_MARKET
from career_engine.publication_audit import build_ledger,write_ledger
from career_engine.detail_allocation import description_completeness


def regional_candidates(transport,registry,base,employer_budget=0,sitemap_budget=2,market=DC_MARKET,usajobs_pages=6):
    """Collection evidence, not fit scores; callers may freeze it for a paired run."""
    rows,stats=retrieve_impact(transport)
    source_stats={'impactopportunity':stats}
    if market.identifier=='dc-dmv':
        from career_engine.regional_usajobs import collect_usajobs
        federal,federal_stats=collect_usajobs(usajobs_pages)
        rows=rows+federal;source_stats['usajobs']=federal_stats
    source_seeds(registry,base['records']+rows,market)
    logs={'public_sitemaps':discover_jazz_sitemap(transport,registry,sitemap_budget,market)}
    if employer_budget and market.identifier=='dc-dmv':
        try:logs['employer_directory']=discover_directory(transport,registry)
        except (FetchError,ValueError) as e:logs['employer_directory']={'status':str(e),'new_employer_sites':0}
        logs['employer_link_checks']=discover_employer_links(transport,registry,base['records']+rows,employer_budget)
    else:
        logs['employer_directory']={'status':'not_requested_optional_zero_budget','new_employer_sites':0}
        logs['employer_link_checks']=[]
    return {'records':rows,'stats':source_stats,'discovery':logs}


def write_capture(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    columns=['identity','source','source_id','board_key','title','company','company_evidence','location','geography',
             'url','posted_at','updated_at','closing_at','retrieved_at','record_kind','evidence_status','detail_status',
             'duplicate_status','description','description_truncated','early_career_signal','function_signals',
             'market_id','collection_mode','worksite_evidence','board_selection_route','previously_seen_in_this_registry','first_observed_run','description_changed_since_prior_observation',
             'index_raw_locations','detail_raw_locations','index_fields','detail_fields','inventory_search_evidence','description_format','offices','raw_provider_record']
    with path.with_suffix('.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=columns);w.writeheader()
        for r in data['records']:
            values={k:json.dumps(r.get(k),ensure_ascii=False) if isinstance(r.get(k),(list,dict)) else r.get(k,'') for k in columns}
            values['collection_mode']=data.get('collection_context',{}).get('mode','')
            w.writerow(values)


def expand(base_path,registry_path,output_dir,combined_path,board_budget=30,seed_queries=6,imports=None,transport=None,extra_details=20,
           strategy='legacy',employer_budget=0,sitemap_budget=2,candidate_capture=None,as_of=None,market=DC_MARKET,usajobs_pages=6):
    base_path,registry_path,output_dir,combined_path=map(Path,(base_path,registry_path,output_dir,combined_path))
    output_dir.mkdir(parents=True,exist_ok=True)
    original=base_path.read_bytes();base=json.loads(original)
    if not isinstance(base.get('records'),list) or not isinstance(base.get('stats'),dict):raise ValueError('Expected an unranked source audit')
    registry=read_registry(registry_path) if registry_path.exists() else empty_registry()
    if strategy not in ('legacy','regional'):raise ValueError('Invalid discovery strategy')
    transport=transport or (EmployerTransport if strategy=='regional' else PublicTransport)(max_requests=min(300,board_budget*6+seed_queries*4+10))
    for host,until in registry.get('host_not_before',{}).items():
        try:
            if datetime.fromisoformat(until)>datetime.now(timezone.utc):transport.blocked.add(host)
        except (ValueError,TypeError):pass
    before=set(registry['boards']);source_added=source_seeds(registry,base['records'],market)
    for observation in base.get('publication_observations',[]):
        for edge in observation.get('channels',[]):
            if edge.get('source_kind')=='public_ats_candidate':
                register(registry,edge['url'],{'kind':'publication_document_link','url':observation['url'],'observed_url':edge.get('observed_url',edge['url'])})
    supplemental=candidate_capture or (regional_candidates(transport,registry,base,employer_budget,sitemap_budget,market,usajobs_pages) if strategy=='regional' else {'records':[],'stats':{},'discovery':{}})
    if imports:
        for entry in imports:
            if not isinstance(entry,dict) or not isinstance(entry.get('url'),str) or not safe_public_url(str(entry.get('evidence_url',''))):continue
            register(registry,entry['url'],{'kind':'explicit_public_URL_evidence_import','url':entry['evidence_url'],
                     'query_family':entry.get('query_family','unrestricted'),'note':'Imported URL evidence, not an employer name allowlist; live provider response still required.'})
    seed_log=discover_commoncrawl(transport,registry,seed_queries,include_icims=strategy=='regional')
    selected=select_regional_boards(registry,board_budget,market=market) if strategy=='regional' else select_boards(registry,board_budget)
    records=[];board_stats=[];stop_reason=''
    for entry in selected:
        stop_reason=transport.global_stop_reason()
        if stop_reason:break
        rows,stats=retrieve_board(transport,entry,detail_budget=0 if strategy=='regional' else 3,market=market)
        stats['selection_evidence']=entry.get('selection_evidence',{'route':'legacy_provider_spread'})
        entry.update({k:stats[k] for k in ('status','dc_dmv','unique','descriptions')})
        entry['last_checked_run']=registry['run'];entry['last_checked_at']=stamp();entry['last_errors']=stats['errors']
        for row in rows:
            row['board_discovery_evidence']=entry['evidence']
            row['board_selection_route']=stats['selection_evidence']['route']
        records+=rows;board_stats.append(stats)
        if len(records)>=20000:
            board_stats[-1]['errors'].append('run_record_cap');stop_reason='run_record_cap';break
        # An individual oversized response or denied host is not a global stop.
    attempted={s['board_key'] for s in board_stats}
    not_attempted=[{'board_key':e['key'],'provider':e['provider'],'reason':stop_reason or 'global_limit'} for e in selected if e['key'] not in attempted]
    detail_before=description_completeness(records,market)
    extra_log=enrich_regional_metadata(transport,records,registry['boards'],extra_details,registry['run'],market,adaptive=strategy=='regional')
    detail_after=description_completeness(records,market)
    for stats in board_stats:
        related=[x for x in extra_log if x['board_key']==stats['board_key']]
        stats['extra_detail_requests']=sum(x['requests'] for x in related);stats['requests']+=stats['extra_detail_requests']
        stats['descriptions']=sum(bool(r['description']) for r in records if r['board_key']==stats['board_key'])
        if 'inventory' in stats:stats['inventory']['descriptions_observed']=stats['descriptions']
        stats['errors'] += ['extra_detail_'+x['status'] for x in related if x['status']!='ok']
        if stats['errors'] and stats['unique']:stats['status']='partial_failure'
        registry['boards'][stats['board_key']]['descriptions']=stats['descriptions']
    coverage=assess_increment(base['records'],records,as_of,market)
    supplemental_coverage=assess_increment(base['records'],supplemental['records'],as_of,market)
    seen=mark_seen(registry,records+supplemental['records'])
    status='partial_failure' if not_attempted or any(s['errors'] or s['status']!='ok' for s in board_stats) or any(q.get('status')!='ok' for q in seed_log) else 'ok'
    stats={'requests':len(transport.requests),'received':len(records),'unique':len(records),'status':status,
           'errors':(['Some seed queries or boards failed; inspect ATS discovery report'] if status!='ok' else []),
           'capped':len(selected)<len(registry['boards']) or any(s['capped'] for s in board_stats)}
    data={'kind':'UNRANKED PUBLIC ATS DISCOVERY; NOT EMPLOYER-CONFIRMED ACTIVE OR FIT RECOMMENDATIONS',
          'schema_version':'job-source-audit/v1','market':market.asdict(),'collection_context':getattr(transport.session,'collection_context',{'mode':'live_public_get'}),
          'captured_at':stamp(),'records':records,'stats':{'public_ats':stats},'additional_source_stats':supplemental['stats'],'coverage':coverage,
          'discovery':{'registry_boards':len(registry['boards']),'new_registry_boards':len(set(registry['boards'])-before),
                       'strategy':strategy,'regional_evidence':supplemental['discovery'],'cross_run_observations':seen,
                       'publication_observations':transport.publication_observations,
                       'source_link_boards_added':source_added,'seed_queries':seed_log,'selected_boards':len(selected),
                       'attempted_boards':len(board_stats),'not_attempted_boards':not_attempted,
                       'selected_board_plan':[{'board_key':e['key'],'provider':e['provider'],'selection_evidence':e.get('selection_evidence',{'route':'legacy_provider_spread'})} for e in selected],
                       'platform_counts':{'selected':dict(Counter(e['provider'].removesuffix('_eu') for e in selected)),
                           'attempted':dict(Counter(registry['boards'][s['board_key']]['provider'].removesuffix('_eu') for s in board_stats))},
                       'description_completeness':{'before_shared_inspection':detail_before,'after_shared_inspection':detail_after,
                           'base_page_budget':extra_details,'maximum_conditional_pages':min(60,extra_details*2) if strategy=='regional' else extra_details},
                       'platform_funnel':{p:{'newly_discovered':sum(b['key'] not in before and b['provider'].removesuffix('_eu')==p for b in registry['boards'].values()),
                           'discovered':sum(b['provider'].removesuffix('_eu')==p for b in registry['boards'].values()),
                           'attempted':sum(registry['boards'][b['board_key']]['provider'].removesuffix('_eu')==p for b in board_stats),
                           'index_records':sum(r['provider'].removesuffix('_eu')==p for r in records),
                           'described_records':sum(r['provider'].removesuffix('_eu')==p and bool(r['description']) for r in records)}
                           for p in sorted({b['provider'].removesuffix('_eu') for b in registry['boards'].values()})},
                       'boards':board_stats,'extra_detail_inspections':extra_log,'unsupported_platform_links':registry['unsupported']},
          'transport':{'requests':transport.requests,'bytes':transport.bytes,'maximum_requests':transport.max_requests},
          'baseline_sha256':hashlib.sha256(original).hexdigest(),
          'limits':'Bounded index samples and boards; wildcard/prefix metadata search is not a full-text DC employment index. Every retrieved row remains auditable; no fit filtering.'}
    write_capture(output_dir/'ats-source-audit.json',data)
    supplemental_audit={'kind':'UNRANKED ADDITIONAL SOURCE AUDIT; NOT CONFIRMED OPENINGS OR RECOMMENDATIONS',
                        'captured_at':data['captured_at'],**supplemental,'coverage':supplemental_coverage}
    write_capture(output_dir/'regional-source-audit.json',supplemental_audit)
    combined={'kind':'FULL UNRANKED FIVE-SOURCE + PUBLIC ATS + ADDITIONAL SOURCE AUDIT; DUPLICATE SIGHTINGS RETAINED',
              'schema_version':'job-collection/v1','market':market.asdict(),'collection_context':data['collection_context'],'captured_at':data['captured_at'],'original_five_source_capture_at':base.get('captured_at',''),
              'records':base['records']+records+supplemental['records'],'stats':{**base['stats'],'public_ats':stats,**supplemental['stats']},
              'ats_coverage':coverage,'additional_source_coverage':supplemental_coverage,'cross_run_observations':seen,
              'original_five_source_sha256':data['baseline_sha256']}
    write_capture(combined_path,combined)
    ledger=build_ledger(base,data,supplemental,registry,market)
    write_ledger(output_dir/'coverage-audit.json',ledger)
    for request in transport.requests:
        if request.get('status') in (403,429):
            wait=86400
            if request.get('status')==429 and str(request.get('retry_after','')).isdigit():wait=max(60,min(int(request['retry_after']),604800))
            registry['host_not_before'][urlsplit(request['url']).hostname]=(datetime.now(timezone.utc)+timedelta(seconds=wait)).isoformat()
    def c(v):return str(v).replace('|','\\|').replace('\n',' ')
    lines=['# Public ATS discovery — unranked','',
           'The original five-source audit is unchanged. ATS publication is observed, not independent confirmation of accepting applications or candidate qualification. No paid search/API or model calls.',
           '', '## Measured coverage','', '```json',json.dumps(coverage,indent=2),'```','',
           f"Registry: {len(registry['boards'])} discovered board candidates; {len(selected)} selected; {len(board_stats)} attempted; {len(not_attempted)} not attempted; {len(transport.requests)} requests; {transport.bytes} response bytes.",'',
           '## Observed local description completeness','', '```json',json.dumps(detail_after,indent=2),'```','',
           '## Selected but not attempted','', '```json',json.dumps(not_attempted,indent=2),'```','',
           '## Board access','', '| Board | Status | Rows | DC/DMV labels | Descriptions | Errors |','|---|---|---:|---:|---:|---|']
    for s in board_stats:lines.append(f"| {c(s['board_key'])} | {s['status']} | {s['unique']} | {s['dc_dmv']} | {s['descriptions']} | {c(', '.join(s['errors']))} |")
    lines+=['','## New regional records requiring review','',
            'No match to this bounded five-source capture is not proof of unique vacancy, suitability, pay, level or comprehensive discovery. Title/description signals are for review only.','',
            '| Role | Employer label | Original location | Duplicate status | Inspection signal (not fit) |','|---|---|---|---|---|']
    for r in records:
        if r['geography']=='dc_dmv':lines.append(f"| [{c(r['title'])}]({r['url']}) | {c(r['company'])} | {c(r['location'])} | {r['duplicate_status']} | {r['early_career_signal']['kind']} |")
    lines+=['','## Seed query outcomes','', '```json',json.dumps(seed_log,indent=2),'```','',data['limits'],'']
    lines+=['## Regional discovery and additional feed evidence','','```json',json.dumps(supplemental['discovery'],indent=2),'```',
            '', 'Additional feed/API records and configuration status are retained in regional-source-audit.json/.csv, with source attribution, retrieval dates and inventory windows. These are separate from ATS counts. This unofficial tool is not operated, sponsored, approved or endorsed by OPM.',
            '', '```json',json.dumps(supplemental_coverage,indent=2),'```','','Cross-run first observations are specific to this saved registry, not new market vacancies.','']
    (output_dir/'ats-source-audit.md').write_text('\n'.join(lines),encoding='utf-8')
    combined_path.with_suffix('.md').write_text('# Expanded unranked audit\n\nThe full JSON/CSV retains every original, ATS and additional public-feed record, including duplicates and uncertain/off-market records. See the original dc-source-audit.md, ats-discovery/ats-source-audit.md and regional-source-audit.json/.csv.\n',encoding='utf-8')
    if base_path.read_bytes()!=original:raise RuntimeError('Original five-source audit changed')
    # Commit mutable state only after every raw observation/report was written.
    save_registry(registry_path,registry)
    return data


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base',default='output/dc-source-audit.json');p.add_argument('--registry',default='data/ats-registry.json')
    p.add_argument('--output-dir',default='output/ats-discovery');p.add_argument('--combined-output',default='output/dc-expanded-audit.json')
    p.add_argument('--board-budget',type=int,default=30);p.add_argument('--seed-queries',type=int,default=6)
    p.add_argument('--extra-details',type=int,default=20,help='Shared regional metadata description budget (0–30), after board discovery')
    p.add_argument('--usajobs-pages',type=int,default=6,help='Existing USAJOBS collector: DC regional only, 1–20 pages when credentials and terms are configured')
    p.add_argument('--evidence-import',help='Optional JSON list of public ATS URLs with evidence_url; never company-name guesses')
    p.add_argument('--strategy',choices=('regional','legacy'),default='regional',help='Regional source evidence with at least 20%% non-targeted board exploration, or prior provider spread')
    p.add_argument('--employer-budget',type=int,default=0,help='Optional observed employer websites to inspect (0–30, off by default), up to three public HTML pages each')
    p.add_argument('--sitemap-budget',type=int,default=2,help='Advertised JazzHR sitemap prefixes (0–5), 1.5 MB per prefix')
    args=p.parse_args(argv)
    if not 1<=args.usajobs_pages<=20 or not 1<=args.board_budget<=100 or not 0<=args.seed_queries<=20 or not 0<=args.extra_details<=30 or not 0<=args.employer_budget<=30 or not 0<=args.sitemap_budget<=5:p.error('Invalid ATS budget')
    try:
        imports=json.loads(Path(args.evidence_import).read_text()) if args.evidence_import else None
        if imports is not None and not isinstance(imports,list):p.error('Evidence import must be a list')
        data=expand(args.base,args.registry,args.output_dir,args.combined_output,args.board_budget,args.seed_queries,imports,extra_details=args.extra_details,
                    strategy=args.strategy,employer_budget=args.employer_budget,sitemap_budget=args.sitemap_budget,usajobs_pages=args.usajobs_pages)
    except (OSError,ValueError,RuntimeError,KeyError) as e:
        print(json.dumps({'status':'failed','error_type':type(e).__name__,'original_audit':'preserved; see existing files'}));return 1
    print(json.dumps({'coverage':data['coverage'],'discovered_boards':data['discovery']['registry_boards'],
                      'selected_boards':data['discovery']['selected_boards'],'status':data['stats']['public_ats']['status']}))
    return 0 if any(s['status']!='failed' for s in data['discovery']['boards']) or any(s.get('status')=='ok' for s in data['additional_source_stats'].values()) else 1


if __name__=='__main__':raise SystemExit(main())
