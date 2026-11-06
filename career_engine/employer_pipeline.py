"""Bounded employer → public hiring-channel evidence → existing ATS collectors.

This is source discovery, not candidate ranking or a new platform adapter.
Unknown identity and inventory states are useful outputs, never zero vacancies.
"""
import copy
import hashlib
import re
from datetime import datetime,timezone,timedelta
from urllib.parse import urlsplit,urljoin,unquote,urlunsplit
from urllib.robotparser import RobotFileParser

from career_engine.ats import board_from_url,retrieve_board,FetchError,stamp
from career_engine.ats_registry import register,observed_ats_urls,UNSUPPORTED
from career_engine.dc_sources import Document
from career_engine.employer_discovery import EmployerTransport,website_url
from career_engine.extract import safe_public_url,strip_html,USER_AGENT
from career_engine.public_sources import public_text
from career_engine.markets import DC_MARKET
from career_engine.regional_selection import mark_seen

CAREER=re.compile(r'(?i)\b(careers?|jobs?|employment|vacanc(?:y|ies)|join (?:our|the) team|work (?:with|for) us)\b')
SECONDARY=('linkedin.com','indeed.com','idealist.org','ziprecruiter.com','washingtonpost.com','workforgood.org','daybook.com')
GENERIC={'the','and','llc','llp','inc','incorporated','corp','corporation','company','co','group','services','service','associates','association','of','at','com','home','welcome'}


def clean_excerpt(text,limit=220):
    text=re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[email redacted]',text)
    text=re.sub(r'(?<!\w)(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}(?!\w)','[phone redacted]',text)
    return re.sub(r'\s+',' ',text).strip()[:limit]


def host(url):
    try:return (urlsplit(url).hostname or '').lower().removeprefix('www.')
    except (ValueError,TypeError):return ''


class PipelineTransport(EmployerTransport):
    """Extend existing safety with robots checks at every followed redirect."""
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.redirect_leads=[]

    def redirect_allowed(self,original_host,target):
        if not super().redirect_allowed(original_host,target):
            lead=channel_url(target)
            if lead:self.redirect_leads.append({'original_host':original_host,'url':lead,'status':'not_followed_unresolved_redirect'})
            return False
        if original_host.endswith('.icims.com') or urlsplit(target).path=='/robots.txt':return True
        h=urlsplit(target).hostname
        if h not in self.robots:
            try:
                text=self.get('https://'+h+'/robots.txt',max_bytes=100000,kind='robots')
                rp=RobotFileParser();rp.parse(text.splitlines());self.robots[h]=rp
            except FetchError as exc:self.robots[h]=True if str(exc)=='http_404' else False
        rp=self.robots[h]
        return rp is True or (rp is not False and rp.can_fetch(USER_AGENT,target) and (rp.crawl_delay(USER_AGENT) or 0)<=1)


def page_identity(html,legal_name):
    doc=Document(html);visible=strip_html(html)
    title=next((n.text() for n in doc.root.walk() if n.tag=='title'),'')
    tokens=lambda value:set(re.findall(r'[a-z0-9]+',re.sub(r'(?<=[a-z])(?=[A-Z])',' ',value).lower()))-GENERIC
    wanted=tokens(legal_name)
    identity_text=visible+' '+' '.join(n.attrs.get('alt','') for n in doc.root.walk() if n.tag=='img')
    observed=tokens(identity_text)
    overlap=wanted&observed
    # Deliberately conservative. A frame website is a lead until name evidence
    # corroborates it; abbreviations, rebrands and affiliates go to review.
    verified=bool(wanted and len(overlap)/len(wanted)>=.75 and any(len(t)>=4 for t in overlap))
    return {'status':'name_corroborated' if verified else 'identity_unresolved',
            'operating_label_observed':clean_excerpt(title,150),'matching_name_tokens':sorted(overlap),
            'legal_entity_match':'public_name_corroboration_not_legal_employer_verification',
            'parent_affiliate_status':'not_established','employing_legal_entity_status':'not_established_from_website_title',
            'relationship_leads':[clean_excerpt(m.group(0)) for m in re.finditer(r'(?i).{0,45}(?:subsidiary of|part of|affiliate of|formerly known as).{0,90}',visible)][:3]}


def channel_url(value):
    """Preserve meaningful public query IDs, reject access-bearing/private URLs."""
    if not safe_public_url(value):return ''
    u=urlsplit(value)
    if u.scheme!='https':
        if u.scheme!='http':return ''
        value=urlunsplit(('https',u.netloc,u.path,u.query,''));u=urlsplit(value)
    if re.search(r'(?i)(?:token|auth|signature|email|session|password|key)=',u.query):return ''
    if re.search(r'(?i)/(?:login|admin|wp-admin|account|sign.?in)(?:/|$)',unquote(u.path)):return ''
    if any(unquote(p) in ('.','..') or '/' in unquote(p) or '\\' in unquote(p) for p in u.path.split('/')):return ''
    return urlunsplit(('https',u.netloc,u.path or '/',u.query,''))


def extract_channels(html,page_url,site_url):
    """Observed links only. No guessed career paths, tenants or hidden endpoints."""
    doc=Document(html);leads={}
    def add(url,kind,text='',**extra):
        key=(kind,url)
        if key not in leads:leads[key]={'url':url,'kind':kind,'observed_on':page_url,'link_text':clean_excerpt(text,120),**extra}
    for node in doc.root.walk():
        if node.tag not in ('a','iframe'):continue
        href=node.attrs.get('href') or node.attrs.get('src') or '';text=node.text()
        if href.lower().startswith('mailto:'):
            address=href[7:].split('?',1)[0]
            local,sep,domain=address.partition('@')
            if sep and (CAREER.search(text) or re.fullmatch(r'(?i)(?:jobs|careers|recruiting|recruitment|hr|employment|resumes|talent|apply)',local)):
                # Keep functional application inboxes; redact personal local-parts.
                public_local=local if re.fullmatch(r'(?i)(?:jobs|careers|recruiting|recruitment|hr|employment|resumes|talent|apply)',local) else '[redacted]'
                add('mailto:'+public_local+'@'+domain.lower(),'email_application',text,
                    original_uri_sha256=hashlib.sha256(href.encode()).hexdigest())
            continue
        url=channel_url(urljoin(page_url,href))
        if not url:continue
        b=board_from_url(url)
        h=host(url)
        if b:add(url,'supported_ats',text,board_key=b['key'],canonical_board=b['url'],provider=b['provider']);continue
        unsupported=next((p for p in UNSUPPORTED if h==p or h.endswith('.'+p)),None)
        relevant=bool(CAREER.search(text+' '+unquote(urlsplit(url).path)))
        if unsupported and (relevant or node.tag=='iframe'):add(url,'unsupported_ats',text,provider=unsupported);continue
        if not relevant:continue
        if urlsplit(url).path.lower().endswith('.pdf'):kind='employer_pdf'
        elif any(h==x or h.endswith('.'+x) for x in SECONDARY):kind='secondary_board'
        elif h==host(site_url):kind='employer_html'
        else:kind='external_hiring_channel'
        add(url,kind,text)
    # Public, explicit embedded ATS URLs count as links; do not synthesize tokens
    # from script identifiers, network calls or private APIs.
    for url in observed_ats_urls(html):
        b=board_from_url(url)
        if b:add(url,'supported_ats','embedded public ATS URL',board_key=b['key'],canonical_board=b['url'],provider=b['provider'])
        else:
            asset=bool(re.search(r'(?i)\.(?:js|css|png|jpg|svg)$',urlsplit(url).path))
            add(url,'unsupported_platform_reference' if asset else 'unsupported_ats','embedded public platform reference' if asset else 'embedded public ATS URL')
    return list(leads.values())


def resolve_employer(row,transport,page_cap=3):
    result={'frame_entity':row,'official_site':{'status':'unresolved','candidate':row.get('website_candidate','')},
            'entity_relationship':{'status':'not_established'},'pages':[],'channels':[],'review_queue':[],
            'discovery_state_before':'not_matched_to_saved_registry','vacancy_state':'unknown_not_zero'}
    candidate=website_url(row.get('website_candidate',''))
    if not candidate:
        result['review_queue'].append({'reason':'no_safe_official_site','next_action':'research_official_site_from_public_entity_evidence'})
        return result
    queue=[candidate];visited=set();channel_keys=set();verified=False
    while queue and len(visited)<page_cap:
        url=queue.pop(0)
        if url in visited:continue
        visited.add(url)
        page={'url':url,'status':'not_attempted'};result['pages'].append(page)
        redirects_before=len(getattr(transport,'redirect_leads',[]))
        try:
            if not transport.observe(url):raise FetchError('unapproved_or_nonpublic_employer_URL')
            html=public_text(transport,url,max_bytes=600000,kind='public_html',prefix=True)
            final_url=next((q['url'] for q in reversed(transport.requests) if q.get('kind')=='public_html' and q.get('status')==200),url)
            request=next((q for q in reversed(transport.requests) if q.get('kind')=='public_html'),{})
            prefix_limited=bool(request.get('truncated'))
            page.update(status='retrieved',final_url=final_url,observed_at=stamp(),sha256=hashlib.sha256(html.encode()).hexdigest(),
                        sha256_scope='retrieved_prefix_only' if prefix_limited else 'complete_response',bytes=len(html.encode()),prefix_limited=prefix_limited)
            if prefix_limited:
                result['review_queue'].append({'reason':'page_prefix_limited','url':url,'next_action':'review_remaining_publication_links_without_increasing_caps'})
                html=html[:html.rfind('>')+1] if '>' in html else ''
            if url==candidate:
                identity=page_identity(html,row['legal_name']);result['entity_relationship']=identity
                verified=identity['status']=='name_corroborated'
                result['official_site'].update(status='verified' if verified else 'identity_unresolved',url=candidate,
                    evidence_url=url,name_evidence=identity['matching_name_tokens'])
                if not verified:result['review_queue'].append({'reason':'identity_conflict_or_insufficient_name_evidence','next_action':'verify_operating_entity_parent_or_rebrand'})
            leads=extract_channels(html,final_url,candidate)
            for lead in leads:
                key=(lead['kind'],lead['url'])
                if key not in channel_keys:
                    if len(result['channels'])>=200:
                        warning={'reason':'employer_channel_cap_200','url':url,'next_action':'review_remaining_public_links_within_separate_budget'}
                        if warning not in result['review_queue']:result['review_queue'].append(warning)
                        continue
                    lead.update(identity_verified=verified,access_state='observed_link_not_inventory_verified',
                        next_action='existing_collector' if lead['kind']=='supported_ats' else 'review_public_hiring_channel')
                    result['channels'].append(lead);channel_keys.add(key)
                if verified and lead['kind']=='employer_html' and website_url(lead['url']) and lead['url'] not in visited:
                    queue.append(lead['url'])
            if not leads and (len(strip_html(html))<200 or re.search(r'(?i)enable javascript|javascript (?:is required|must be enabled)',strip_html(html))):
                result['review_queue'].append({'reason':'javascript_shell_or_sparse_page','url':url,'next_action':'optional_public_research_no_hidden_API'})
        except (FetchError,ValueError,RecursionError) as exc:
            page['status']=str(exc)[:100]
            for redirect in getattr(transport,'redirect_leads',[])[redirects_before:]:
                result['channels'].append({'url':redirect['url'],'kind':'unresolved_redirect','observed_on':url,
                    'identity_verified':False,'access_state':redirect['status'],'next_action':'verify_parent_rebrand_or_official_redirect_before_request'})
            result['review_queue'].append({'reason':page['status'],'url':url,'next_action':'review_access_or_official_site_evidence_no_bypass'})
        if transport.global_stop_reason():break
    fetched={u for p in result['pages'] if p['status']=='retrieved' for u in (p['url'],p.get('final_url',p['url']))}
    for lead in result['channels']:
        if lead['url'] in fetched:lead['access_state']='public_endpoint_retrieved_inventory_unknown'
    if not result['channels']:
        result['review_queue'].append({'reason':'no_hiring_channel_observed','next_action':'research_publication_evidence_not_zero_vacancies'})
    return result


def registry_match(row,registry):
    """Match external entity IDs or exact source website evidence, never name/slug guesses."""
    candidate=website_url(row.get('website_candidate',''));matches=[]
    for key,b in registry['boards'].items():
        for evidence in b.get('evidence',[]):
            same_entity=evidence.get('frame_id')==row['frame_id']
            url=evidence.get('url','')
            same_site=bool(candidate and safe_public_url(url) and host(url)==host(candidate))
            if same_entity or same_site:matches.append(key);break
    return sorted(set(matches))


def finalize_evidence(employers):
    """Pure evidence labeling/export; also usable on retained observation captures."""
    technical_unresolved=0
    for e in employers:
        technical_unresolved+=any(not q['reason'].startswith('inventory_unknown_') for q in e['review_queue'])
        if e.get('entity_relationship',{}).get('status')=='name_corroborated':
            e['entity_relationship'].setdefault('employing_legal_entity_status','not_established_from_website_title')
        for c in e['channels']:
            if c['kind']=='unsupported_ats' and re.search(r'(?i)\.(?:js|css|png|jpg|svg)$',urlsplit(c['url']).path):
                c['kind']='unsupported_platform_reference'
            if c['kind']=='supported_ats':continue
            q={'reason':'inventory_unknown_'+c['kind'],'url':c['url'],
               'next_action':'review_public_channel_without_new_adapter_or_submissions'}
            if q not in e['review_queue']:e['review_queue'].append(q)
    return technical_unresolved


def run_pipeline(frame,registry,discovery_transport,collection_transport,page_cap=3,board_cap=15,detail_budget=2):
    if not 1<=len(frame['rows'])<=40 or not 1<=page_cap<=3 or not 0<=board_cap<=40 or not 0<=detail_budget<=30:raise ValueError('Invalid employer discovery bounds')
    prior=copy.deepcopy(registry);employers=[];board_entities={};records=[];inventories=[];active_cooldowns=set()
    for h,until in registry.get('host_not_before',{}).items():
        try:
            if datetime.fromisoformat(until)>datetime.now(timezone.utc):
                discovery_transport.blocked.add(h);active_cooldowns.add(h)
        except (ValueError,TypeError):pass
    for row in frame['rows']:
        if row['eligibility']!='eligible':
            employers.append({'frame_entity':row,'status':'eligibility_unknown' if row['eligibility']=='unknown' else 'ineligible',
                              'channels':[],'review_queue':[{'reason':'eligibility_unknown','next_action':'verify_external_frame_eligibility'}] if row['eligibility']=='unknown' else []});continue
        # Even when the budget is exhausted, emit the remaining unresolved rows.
        if discovery_transport.global_stop_reason():
            e={'frame_entity':row,'official_site':{'status':'not_attempted'},'channels':[],
               'review_queue':[{'reason':discovery_transport.global_stop_reason(),'next_action':'resume_within_authorized_budget'}]}
        else:
            try:e=resolve_employer(row,discovery_transport,page_cap)
            except Exception as exc:
                e={'frame_entity':row,'official_site':{'status':'unresolved'},'channels':[],
                   'review_queue':[{'reason':'source_adapter_exception_'+type(exc).__name__,'next_action':'review_adapter_without_erasing_other_employers'}]}
        known=registry_match(row,prior)
        observed={c['board_key'] for c in e['channels'] if c['kind']=='supported_ats'}
        known=sorted(set(known)|{k for k in observed if k in prior['boards']})
        candidate=website_url(row.get('website_candidate',''))
        known_site=any(candidate and host(site.get('url',''))==host(candidate) for site in prior.get('employer_sites',{}).values())
        e['known_board_keys_before']=known
        e['discovery_state_before']=('known_board_never_polled' if any(prior['boards'][k].get('last_checked_run') is None for k in known)
                                     else 'known_board_previously_polled' if known else 'known_employer_site_no_board' if known_site else 'not_matched_to_saved_registry')
        e['coverage_gaps']=[]
        if not known:e['coverage_gaps'].append('employer_not_linked_in_saved_registry')
        if any(prior['boards'][k].get('last_checked_run') is None for k in known):e['coverage_gaps'].append('known_endpoint_poll_priority_gap')
        if any(c['kind']!='supported_ats' for c in e['channels']):e['coverage_gaps'].append('unsupported_or_unconventional_channel_evidence')
        for c in e['channels']:
            if c['kind']!='supported_ats' or not c['identity_verified']:continue
            register(registry,c['url'],{'kind':'regional_employer_public_link','frame_id':row['frame_id'],'frame_legal_name':row['legal_name'],
                     'url':c['observed_on'],'observed_url':c['url'],'frame_source_url':row['source_url'],
                     'frame_source_sha256':row['source_sha256'],'location':'Washington, DC','geography_hint':'employer_headquarters_only',
                     'note':'External frame + corroborated website. Employer headquarters does not establish a job worksite.'})
            key=c['board_key']
            if key not in registry['boards']:
                c['access_state']='registry_capacity_not_registered';continue
            board_entities.setdefault(key,set()).add(row['frame_id'])
            c['registry_state_before']='known_never_polled' if key in prior['boards'] and prior['boards'][key].get('last_checked_run') is None else 'known_previously_polled' if key in prior['boards'] else 'new_board'
        if e.get('official_site',{}).get('status')=='verified':
            site_url=e['official_site']['url'];site_key=hashlib.sha256(site_url.encode()).hexdigest()[:24]
            sites=registry.setdefault('employer_sites',{})
            if site_key in sites or len(sites)<5000:
                prior_leads=sites.get(site_key,{}).get('channel_leads',[])
                merged={(c['kind'],c['url'],c.get('observed_on','')):c for c in prior_leads+e['channels']}
                sites[site_key]={**sites.get(site_key,{}),'url':site_url,'company_label':row['legal_name'],'frame_id':row['frame_id'],
                    'evidence_url':row['source_url'],'source_location':row['footprint'],'last_checked_run':registry['run'],
                    'last_status':'public_website_name_corroborated','channel_leads':list(merged.values())}
        employers.append(e)
    # Start the CLI's independent collection clock after discovery finishes.
    # Caller-supplied existing transports keep their already-consumed budgets.
    if callable(collection_transport):collection_transport=collection_transport()
    collection_transport.blocked.update(active_cooldowns)
    # Poll only verified supported sources observed through these employers.
    for key in list(board_entities)[:board_cap]:
        if collection_transport.global_stop_reason():break
        b=registry['boards'][key]
        try:rows,stats=retrieve_board(collection_transport,b,detail_budget=detail_budget,market=DC_MARKET)
        except Exception as exc:
            rows=[];stats={'board_key':key,'status':'failed','errors':['source_adapter_exception_'+type(exc).__name__],
                           'requests':0,'unique':0,'descriptions':0,'dc_dmv':0}
        stats['frame_entity_ids']=sorted(board_entities[key]);inventories.append(stats)
        b.update(last_checked_run=registry['run'],last_checked_at=stamp(),status=stats['status'],last_errors=stats['errors'],
                 unique=stats['unique'],descriptions=stats['descriptions'],dc_dmv=stats['dc_dmv'])
        for row in rows:
            row['employer_frame_entity_ids']=stats['frame_entity_ids']
            row['employer_attribution']='observed_board_group_not_proof_each_affiliate_is_the_legal_employer'
            records.append(row)
    stats_by_key={s['board_key']:s for s in inventories}
    for e in employers:
        for c in e['channels']:
            if c['kind']!='supported_ats' or not c['identity_verified']:continue
            stats=stats_by_key.get(c['board_key'])
            c['access_state']=stats['status'] if stats else 'not_attempted_within_collection_cap'
            c['inventory_attempted']=bool(stats);c['index_records']=stats['unique'] if stats else None
            c['described_records']=stats['descriptions'] if stats else None
            c['regional_records']=stats['dc_dmv'] if stats else None
            c['regional_location_labels_observed']=sorted({r['location'] for r in records if r['board_key']==c['board_key'] and r.get('geography')=='dc_dmv'}) if stats else []
            c['location_verification_scope']='published offering labels only; employer headquarters is not job worksite verification'
            if stats and (stats['errors'] or stats['status']!='ok'):
                e['review_queue'].append({'reason':'inventory_'+stats['status'],'url':c['url'],'next_action':'review_collector_status_no_zero_inference'})
    technical_unresolved=finalize_evidence(employers)
    seen=mark_seen(registry,records) if records else {}
    local=[r for r in records if r.get('geography')=='dc_dmv'];local_ids={r['identity'] for r in local}
    verified_entities={e['frame_entity']['frame_id'] for e in employers if e.get('official_site',{}).get('status')=='verified'}
    review=[{'frame_id':e['frame_entity']['frame_id'],'legal_name':e['frame_entity']['legal_name'],**q} for e in employers for q in e['review_queue']]
    metrics={'eligible_employers':sum(r['eligibility']=='eligible' for r in frame['rows']),'verified_official_sites':len(verified_entities),
             'verified_careers_endpoints':len({e['frame_entity']['frame_id'] for e in employers if any(c['identity_verified'] and (c['access_state']=='public_endpoint_retrieved_inventory_unknown' or c.get('inventory_attempted') and c.get('index_records') is not None and c['access_state']!='failed') for c in e['channels'])}),
             'supported_board_groups_discovered':len(board_entities),'inventories_attempted':len(inventories),
             'entities_with_attempted_inventories':len({f for s in inventories for f in s['frame_entity_ids']}),
             'new_board_groups':sum(k not in prior['boards'] for k in board_entities),
             'known_unpolled_groups':sum(k in prior['boards'] and prior['boards'][k].get('last_checked_run') is None for k in board_entities),
             'regional_posting_identities':len(local_ids),'regional_with_original_descriptions':len({r['identity'] for r in local if r.get('description')}),
             'new_regional_identities_in_saved_history':len({r['identity'] for r in local if not r.get('previously_seen_in_this_registry')}),
             'new_regional_described_in_saved_history':len({r['identity'] for r in local if r.get('description') and not r.get('previously_seen_in_this_registry')}),
             'regional_board_groups':len({r['board_key'] for r in local}),'regional_frame_entities':len({f for r in local for f in r['employer_frame_entity_ids']}),
             'unsupported_channel_leads':sum(c['kind']!='supported_ats' for e in employers for c in e['channels']),
             'employers_with_unsupported_channels':sum(any(c['kind']!='supported_ats' for c in e['channels']) for e in employers),
             'identity_access_unresolved_employers':technical_unresolved,
             'unresolved_employers':sum(bool(e['review_queue']) for e in employers),'review_queue_items':len(review),
             'failed_inventory_groups':sum(s['status']=='failed' for s in inventories)}
    for q in discovery_transport.requests+collection_transport.requests:
        if q.get('status') not in (403,429):continue
        wait=86400
        if q.get('status')==429 and str(q.get('retry_after','')).isdigit():wait=max(60,min(int(q['retry_after']),604800))
        registry['host_not_before'][urlsplit(q['url']).hostname]=(datetime.now(timezone.utc)+timedelta(seconds=wait)).isoformat()
    registry['run']+=1
    collection_state='not_collected' if not inventories else 'partial_failure' if any(s['status']!='ok' for s in inventories) else 'ok'
    return {'schema_version':'employer-discovery/v1','kind':'UNRANKED EMPLOYER FRAME DISCOVERY; NOT MARKET RECALL OR FIT VALIDATION',
            'captured_at':stamp(),'frame_selection':frame.get('selection',{}),'metrics':metrics,'employers':employers,
            'records':records,'stats':{'employer_inventories':{'status':collection_state,
                      'unique':len(records),'errors':['No inventory attempted; source availability is unknown'] if not inventories else [err for s in inventories for err in s['errors']]}},
            'discovery_status':'partial' if review else 'completed_bounded_discovery',
            'inventories':inventories,'review_queue':review,'cross_run_observations':seen,
            'transport':{'discovery':{'requests':discovery_transport.requests,'bytes':discovery_transport.bytes},
                         'collection':{'requests':collection_transport.requests,'bytes':collection_transport.bytes}},
            'limits':'Finite external DC CBE headquarters frame, bounded public pages and supported inventories only. Frame entity, board group and job worksite are separate. No title screening or zero-vacancy inference.'}
