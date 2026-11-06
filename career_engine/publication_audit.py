"""Portable publication-channel discovery and a scoped, evidence-only coverage ledger.

No network, employer allowlist, candidate profile, relevance gate or AI evaluator.
A discovered endpoint is a candidate source, not proof of a live inventory.
"""
import csv
import hashlib
import json
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin,urlsplit,parse_qsl

from career_engine.ats import board_from_url,stamp
from career_engine.extract import safe_public_url
from career_engine.markets import DC_MARKET


def channel_url(value,base=''):
    value=urljoin(base,value)
    if value.startswith('http://'):value='https://'+value[7:]
    if not safe_public_url(value):return ''
    if any(re.search(r'token|secret|password|api.?key|session|authorization',k,re.I) for k,v in parse_qsl(urlsplit(value).query)):return ''
    return value.split('#',1)[0]


def offered_channels(text,source_url):
    """Generic advertised RSS/Atom/sitemap/ATS edges. Never fetch discovered URLs."""
    found={}
    def add(value,kind,label=''):
        url=channel_url(value,source_url)
        original=url
        if kind=='public_ats_candidate' and board_from_url(url):url=board_from_url(url)['url']
        if url:found[(url,kind)]={'url':url,'source_kind':kind,'label':label[:160],
            'observed_url':original,'discovered_from':source_url,'evidence':'explicit_public_document_link','retrieval_state':'not_yet_covered'}
    class Links(HTMLParser):
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs);href=attrs.get('href') or attrs.get('src','')
            mime=attrs.get('type','').lower();rel=attrs.get('rel','').lower()
            if tag in ('link','atom:link') and 'alternate' in rel and ('rss' in mime or 'atom' in mime):add(href,'atom' if 'atom' in mime else 'rss',attrs.get('title',''))
            elif tag=='a' and re.search(r'(?:\.rss(?:\?|$)|/rss/?(?:\?|$)|rss\.php(?:\?|$)|feed\.rss(?:\?|$))',href,re.I):add(href,'rss')
            if href and board_from_url(urljoin(source_url,href)):add(href,'public_ats_candidate')
            elif tag=='a' and re.search(r'/(?:careers?|jobs|employment)(?:/|\?|$)',urlsplit(urljoin(source_url,href)).path,re.I):
                add(href,'career_site_candidate')
    try:Links(convert_charrefs=True).feed(str(text)[:512000])
    except (ValueError,RecursionError):pass
    if urlsplit(source_url).path=='/robots.txt':
        for value in re.findall(r'(?im)^\s*Sitemap\s*:\s*(https?://\S+)',str(text)[:100000]):add(value,'sitemap')
    root=re.search(r'<(rss|feed)\b',str(text)[:1000],re.I)
    if root and channel_url(source_url):
        kind='atom' if root[1].lower()=='feed' else 'rss'
        add(source_url,kind)
        found[(channel_url(source_url),kind)]['evidence']='received_public_feed_document'
    return list(found.values())


class PublicationObserver:
    """Observe responses the existing collector already reads; zero added requests."""
    def __init__(self,session):self.session=session;self.observations=[]
    def get(self,url,*args,**kwargs):
        response=self.session.get(url,*args,**kwargs)
        observation={'url':url,'status':response.status_code,'observed_at':stamp(),'channels':[]}
        if response.status_code==200:
            text=getattr(response,'text','')
            if isinstance(text,str):observation['channels']=offered_channels(text,url)
        self.observations.append(observation)
        return response
    def __getattr__(self,key):return getattr(self.session,key)


def _state(stats):
    errors=' '.join(map(str,stats.get('errors',[])))
    if stats.get('unique',0) and errors:return 'partially_retrieved'
    if re.search(r'403|429|404|network|robots|challenge|host_stopped|stricter_crawl_delay',errors):return 'inaccessible'
    if re.search(r'response_or_byte_limit|response_too_large|request_budget|byte_budget|time_budget',errors):return 'partially_retrieved'
    if errors:return 'not_yet_covered'  # Reachable but empty/unsupported schema is not proven access denial.
    inventory=stats.get('inventory',{})
    return 'searched' if inventory.get('metadata_complete') is True else 'partially_retrieved'


def build_ledger(base,ats=None,supplemental=None,registry=None,market=DC_MARKET):
    ats=ats or {};supplemental=supplemental or {};registry=registry or {'boards':{}}
    entries={};captured=ats.get('captured_at') or base.get('captured_at') or stamp()
    def add(url,kind,evidence=None,**fields):
        url=channel_url(url)
        if not url:return None
        key=hashlib.sha256(url.encode()).hexdigest()[:24]
        entry=entries.setdefault(key,{'id':key,'url':url,'source_kind':kind,'discovery_state':'discovered',
            'retrieval_state':'not_yet_covered','discovery_evidence':[],'discovery_kinds':[],'observed_at':captured})
        if kind not in entry['discovery_kinds']:entry['discovery_kinds'].append(kind)
        if kind!='career_site_candidate':entry['source_kind']=kind
        if evidence and evidence not in entry['discovery_evidence'] and len(entry['discovery_evidence'])<4:entry['discovery_evidence'].append(evidence)
        entry.update(fields);return entry
    for observation in base.get('publication_observations',[])+supplemental.get('publication_observations',[])+ats.get('discovery',{}).get('publication_observations',[]):
        for edge in observation.get('channels',[]):
            add(edge['url'],edge['source_kind'],{'kind':edge['evidence'],'url':edge['discovered_from'],'observed_url':edge.get('observed_url','')},
                retrieval_state='partially_retrieved' if edge['evidence']=='received_public_feed_document' else 'not_yet_covered',
                reason='Received a feed document; feed-specific reconciliation may remain unknown.' if edge['evidence']=='received_public_feed_document' else 'Advertised publication mechanism; job subject, access policy and adapter support are not yet established.')
    for source,stats in base.get('stats',{}).items():
        rows=[r for r in base['records'] if r['source']==source]
        from career_engine.dc_sources import INDEXES
        url=(INDEXES.get(source) or [next((r.get('index_url') for r in rows if r.get('index_url')),'')])[0]
        total=len(rows);details=sum(bool(r.get('description')) for r in rows)
        add(url,'specialty_board',{'kind':'existing_source_collection','source':source},source=source,
            retrieval_state='inaccessible' if not total and stats.get('errors') else 'partially_retrieved',
            errors=stats.get('errors',[]),inventory={'scope':'observed existing-source index/feed union',
                'metadata_retained':total,'publisher_inventory_total':None,'inventory_completeness':None,
                'descriptions_observed':details,'description_denominator_observed_records':total,
                'note':'Legacy received/capped fields mix overlapping feeds and detail limits. No full-source metadata denominator is inferred.'})
    board_stats={s['board_key']:s for s in ats.get('discovery',{}).get('boards',[])}
    pending={s['board_key']:s['reason'] for s in ats.get('discovery',{}).get('not_attempted_boards',[])}
    for key,board in registry.get('boards',{}).items():
        stats=board_stats.get(key)
        evidence=board.get('evidence') or [{'kind':'registry_URL_evidence'}]
        entry=add(board['url'],'public_ats_candidate',evidence[0],platform=board['provider'],board_key=key,
            retrieval_state=_state(stats) if stats else 'not_yet_covered',
            verification='live_public_inventory_observed' if stats and (stats.get('unique') or stats.get('inventory',{}).get('metadata_complete')) else 'URL_candidate_not_verified_live',
            reason='Inventory inspected within this run' if stats else 'Discovered board candidate not polled within this run budget')
        if not entry:continue
        entry['inspection_basis']='current_run_inventory_attempt' if stats else 'discovered_URL_only'
        if key in pending:entry.update(selection_state='selected_not_attempted',not_attempted_reason=pending[key])
        for item in evidence[1:4]:
            if item not in entry['discovery_evidence']:entry['discovery_evidence'].append(item)
        if stats:
            inv=dict(stats.get('inventory',{}));inv.update(metadata_retained=stats['unique'],descriptions_observed=stats['descriptions'],description_denominator_observed_records=stats['unique'])
            available=inv.get('observed_available_items')
            inv['retained_fraction_of_observed_inventory']=stats['unique']/available if isinstance(available,int) and available>0 else None
            entry.update(inventory=inv,errors=stats['errors'],selection_evidence=stats.get('selection_evidence',{}),
                requests=stats['requests'],in_market_labels=sum(r.get('geography')==market.local_label for r in ats.get('records',[]) if r['board_key']==key))
    for source,stats in supplemental.get('stats',{}).items():
        rows=[r for r in supplemental.get('records',[]) if r['source']==source]
        if source=='usajobs':
            add('https://data.usajobs.gov/api/search','documented_api',{'kind':'authorized_API_adapter','source':source},source=source,
                retrieval_state='not_collected' if stats.get('status')=='not_collected' else _state(stats),
                configured=stats.get('configured',False),terms_confirmed=stats.get('terms_confirmed',False),
                setup_requirements=stats.get('setup_requirements',''),errors=stats.get('errors',[]),inventory=stats.get('inventory',{}))
            continue
        url=next((r.get('index_url') for r in rows if r.get('index_url')),'https://impactopportunity.org/rss/')
        total=len(rows)
        add(url,'rss',{'kind':'published_job_feed_adapter','source':source},source=source,
            retrieval_state='partially_retrieved' if total else _state(stats),errors=stats.get('errors',[]),
            inventory={'scope':'returned RSS window, not all publisher jobs','observed_available_items':total if total else None,
                'metadata_retained':total,'retained_fraction_of_observed_inventory':1.0 if total else None,
                'publisher_inventory_total':None,'descriptions_observed':sum(bool(r.get('description')) for r in rows),
                'requirements_completeness':'not_established; a published body may omit qualifications',
                'note':'Full returned feed window parsed; provider may limit its size. No publisher-wide completeness fraction.'})
    for seed in ats.get('discovery',{}).get('regional_evidence',{}).get('public_sitemaps',[]):
        add(seed['url'],'sitemap',{'kind':'advertised_provider_sitemap'},retrieval_state='partially_retrieved' if seed.get('complete_URL_entries') else 'inaccessible',
            inventory={'scope':'bounded sitemap prefix','complete_URL_entries_observed':seed.get('complete_URL_entries',0),
                'total_URL_entries':None,'prefix_limited':True,'note':'URL seeds only; neither vacancies nor verified live boards.'},errors=[] if seed.get('status')=='ok' else [seed.get('status')])
    for lead in registry.get('unsupported',[]):
        parsed=board_from_url(lead.get('url',''))
        if parsed:
            # Keep the original edge while updating capability classification.
            add(parsed['url'],'public_ats_candidate',lead.get('evidence'))
            continue
        add(lead.get('url',''),'unresolved_publication_link',lead.get('evidence'),retrieval_state='not_yet_covered',
            verification='observed_link_not_searched',reason='No supported adapter; one observed URL is not proof of a distinct board, employer or vacancy.')
    if market.identifier=='dc-dmv':
        fixture=Path(__file__).parent/'market_fixtures/dc_publication_gaps.json'
        gaps=json.loads(fixture.read_text()) if fixture.exists() else {'sources':[],'ecosystems':[]}
        for item in gaps['sources']:
            existing=entries.get(hashlib.sha256(channel_url(item['url']).encode()).hexdigest()[:24])
            historical={'observed_local_date':gaps['observed_local_date'],'timezone':gaps['timezone'],'not_rechecked_each_run':True,
                        'state':item['retrieval_state'],'reason':item['reason'],'evidence':item['evidence']}
            if existing and existing.get('inventory'):
                existing['historical_access_observation']=historical
            else:
                add(item['url'],item['source_kind'],item['evidence'],historical_access_observation=historical,
                    inspection_basis='dated_access_investigation_not_rechecked',observed_at=gaps['observed_local_date'],
                    **{k:v for k,v in item.items() if k not in ('url','source_kind','evidence')})
    else:gaps={'ecosystems':[]}
    states=dict(Counter(e['retrieval_state'] for e in entries.values()))
    snapshots=registry.setdefault('source_status_snapshots',{})
    for key,entry in entries.items():
        inv=entry.get('inventory',{})
        old=snapshots.get(key,[])
        snapshots[key]=(old+[{'at':captured,'state':entry['retrieval_state'],'metadata_retained':inv.get('metadata_retained'),
            'observed_available_items':inv.get('observed_available_items')}])[-3:]
    # The registry is bounded independently of an unknown employment universe.
    if len(snapshots)>6000:
        for key in list(snapshots)[:len(snapshots)-6000]:del snapshots[key]
    return {'schema_version':'publication-coverage-ledger/v1','market':market.asdict(),'captured_at':captured,
        'collection_context':ats.get('collection_context',{'mode':'live_public_get'}),
        'supported_public_ats_platforms':['greenhouse','lever','ashby','jazzhr','icims'],
        'summary':{'catalogue_entries':len(entries),'discovered_source_candidates':sum(e['source_kind']!='unresolved_publication_link' for e in entries.values()),
            'unresolved_publication_links':sum(e['source_kind']=='unresolved_publication_link' for e in entries.values()),
            'source_kinds':dict(Counter(e['source_kind'] for e in entries.values())),'retrieval_states':states,
            'note':'Source candidates are not necessarily live employers. Counts describe this observed catalog, never all market sources/jobs.'},
        'sources':list(entries.values()),'ecosystem_gaps':gaps['ecosystems'],
        'unknown_boundary':'Unindexed publishers, inaccessible inventories, undiscovered channels and unobserved jobs have no known total. No market-wide coverage percentage is estimated.'}


def write_ledger(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')
    with path.with_suffix('.csv').open('w',newline='',encoding='utf8') as f:
        fields=['id','url','source_kind','platform','retrieval_state','verification','reason','inventory','errors']
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for entry in data['sources']:w.writerow({k:json.dumps(entry[k],ensure_ascii=False) if isinstance(entry.get(k),(dict,list)) else entry.get(k,'') for k in fields})
    def cell(v):return str(v).replace('|','\\|').replace('\n',' ')
    lines=['# Publication coverage ledger','',data['unknown_boundary'],'',f"Discovered candidates: {data['summary']['discovered_source_candidates']}. Retrieval states: {data['summary']['retrieval_states']}.",'',
        'Inventory counts and fractions refer only to the explicitly named source scope. Descriptions and mandatory-qualification completeness are separate; observed jobs are never fit-filtered.','',
        '| Source | Type | State | Retained metadata | Observed denominator | Description text |','|---|---|---|---:|---:|---:|']
    for e in data['sources']:
        i=e.get('inventory',{});lines.append(f"| {cell(e['url'])} | {e['source_kind']} | {e['retrieval_state']} | {i.get('metadata_retained','unknown')} | {i.get('observed_available_items','unknown')} | {i.get('descriptions_observed','unknown')} |")
    lines+=['','## Observed ecosystem gaps','',json.dumps(data['ecosystem_gaps'],indent=2,ensure_ascii=False),'']
    path.with_suffix('.md').write_text('\n'.join(lines))
