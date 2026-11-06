"""Public JazzHR hosted career HTML and JobPosting JSON-LD; no keyed API.

Only published job-description GETs. Never submit forms, fetch candidate state,
use the employer's API key, or call internal JazzHR services.
"""
import hashlib
import json
import re
from difflib import SequenceMatcher
from urllib.parse import urljoin, urlsplit

from career_engine.ats import FetchError, posting_key, canonical_posting_url, stamp
from career_engine.dc_sources import Document
from career_engine.discovery import geography
from career_engine.extract import strip_html
from career_engine.inspection import plan_inspections, identity
from career_engine.public_sources import public_text
from career_engine.worksite_evidence import worksite_evidence


def description_evidence(row,body,format_name,market=None):
    row.update(description=body[:30000],description_sha256=hashlib.sha256(body.encode()).hexdigest(),
        description_truncated=len(body)>30000,detail_status='public_description_observed',
        description_format=format_name,retrieved_at=stamp())
    if re.search(r'(?i)\bpaid board\s*:\s*no\b|\ball roles are voluntary\b',body):
        row['record_kind']='volunteer_public_appointment'
        row['opportunity_kind_evidence']='Published description explicitly states the board is unpaid/voluntary.'
    elif re.search(r'(?i)considered for any available assignments if you register',body):
        row['record_kind']='registration_pool'
        row['opportunity_kind_evidence']='Published description invites registration for future assignments, not a defined vacancy.'
    elif re.search(r'(?i)^consideration for future openings\b',row['title']) and re.search(r'(?i)contact qualified applicants as we approach our recruitment process',body):
        row['record_kind']='registration_pool'
        row['opportunity_kind_evidence']='Published notice invites future-opening consideration before recruitment begins.'
    row.update(worksite_evidence(row,market))
    return row


def jazz_index(html,board,market=None):
    doc=Document(html).root;rows=[];seen=set()
    heading=doc.find(lambda n:n.tag=='title');name=heading.text() if heading else ''
    name=re.sub(r'\s*[-|–]\s*(?:Career Page|Careers|JazzHR).*$', '',name,flags=re.I).strip()
    for card in (n for n in doc.walk() if n.tag=='li' and n.has_class('list-group-item')):
        link=card.find(lambda n:n.tag=='a' and bool(posting_key(urljoin(board['url'],n.attrs.get('href','')))))
        if not link:continue
        url=canonical_posting_url(urljoin(board['url'],link.attrs['href']));key=posting_key(url)
        if not key.startswith(board['key']+':') or key in seen:continue
        seen.add(key)
        locations=card.find(lambda n:n.has_class('list-group-item-text'))
        loc=locations.text() if locations else ''
        rows.append(dict(source='ats_jazzhr',source_id=board['token']+':'+key.rsplit(':',1)[1],identity=key,
            provider='jazzhr',board_key=board['key'],title=link.text(),company=name or board['token'],
            company_evidence='public_board_page_title' if name else 'board_identifier_only_not_legal_employer_verified',
            url=url,index_url=board['url'],location=loc,raw_location=loc,raw_locations=[loc] if loc else [],geography=geography(loc),
            description='',description_sha256='',description_truncated=False,posted_at='',updated_at='',closing_at='',
            retrieved_at=stamp(),detail_status='index_only',record_kind='advertised_position',
            evidence_status='employer_public_career_page_not_independently_verified_accepting_applications'))
    for row in rows:row.update(worksite_evidence(row,market))
    return rows


def jazz_detail(html,row,market=None):
    candidates=[]
    def collect(obj):
        if isinstance(obj,list):
            for item in obj:collect(item)
        elif isinstance(obj,dict):
            types=obj.get('@type',[]);types=[types] if isinstance(types,str) else types
            if 'JobPosting' in types:candidates.append(obj)
            if '@graph' in obj:collect(obj['@graph'])
    for node in Document(html).root.walk():
        if node.tag=='script' and node.attrs.get('type')=='application/ld+json':
            try:collect(json.loads(''.join(x for x in node.children if isinstance(x,str))))
            except (ValueError,TypeError):continue
    norm=lambda x:re.sub(r'[^a-z0-9]+',' ',strip_html(str(x)).lower()).strip()
    candidates.sort(key=lambda o:SequenceMatcher(None,norm(o.get('title','')),norm(row['title'])).ratio(),reverse=True)
    if not candidates:
        # Older public JazzHR templates publish Organization JSON-LD and the
        # complete visible description in this dedicated section, without a
        # JobPosting object. Do not read application fields or unrelated text.
        doc=Document(html).root
        description=doc.find(lambda n:n.attrs.get('id')=='job-description')
        headings=[n.text() for n in doc.walk() if n.tag in ('h1','h2')]
        if not description or not any(SequenceMatcher(None,norm(h),norm(row['title'])).ratio()>=.85 for h in headings):
            raise ValueError('public_description_section_unavailable_or_title_mismatch')
        body=description.text()
        if not body:raise ValueError('public_description_empty')
        return description_evidence(row,body,'public_JazzHR_HTML_job_description_section',market)
    if SequenceMatcher(None,norm(candidates[0].get('title','')),norm(row['title'])).ratio()<.85:raise ValueError('public_JobPosting_title_mismatch')
    obj=candidates[0]
    locations=obj.get('jobLocation',[]);locations=[locations] if isinstance(locations,dict) else locations
    raw=[]
    for loc in locations if isinstance(locations,list) else []:
        address=loc.get('address',{}) if isinstance(loc,dict) else {}
        if isinstance(address,dict):raw.append(', '.join(str(address[k]) for k in ('addressLocality','addressRegion','addressCountry') if isinstance(address.get(k),str) and address[k]))
    company=obj.get('hiringOrganization',{})
    fields={'title':strip_html(obj.get('title','')),'description':strip_html(obj.get('description','')),
            'location':'; '.join(dict.fromkeys(x for x in raw if x)) or row['location'],
            'company':company.get('name') or row['company'] if isinstance(company,dict) else str(company or row['company']),
            'posted_at':str(obj.get('datePosted') or ''),'closing_at':str(obj.get('validThrough') or ''),
            'work_arrangement':'Remote' if str(obj.get('jobLocationType','')).upper()=='TELECOMMUTE' else ''}
    if not fields['description']:raise ValueError('public_JobPosting_description_empty')
    # A redirected/changed page may contain another listing. Do not overwrite it.
    if obj.get('url') and posting_key(obj['url'])!=row['identity']:raise ValueError('posting_ID_mismatch')
    body=fields['description']
    if not fields.get('location'):fields['location']=row['location']
    row.update(fields);row.update(raw_location=row['location'],raw_locations=[row['location']])
    return description_evidence(row,body,'public_JobPosting_JSON_LD',market)


def retrieve_jazzhr(transport,board,detail_budget=1,market=None):
    start=len(transport.requests);rows=[]
    stats=dict(board_key=board['key'],status='ok',errors=[],warnings=[],received=0,malformed=0,unlisted_omitted=0,capped=False,requests=0)
    try:
        html=public_text(transport,board['url'],max_bytes=1_000_000,kind='public_html');rows=jazz_index(html,board,market)
        if not rows:raise FetchError('no_public_index_rows_or_schema_changed')
        stats['received']=len(rows);stats['capped']=len(rows)>1000;rows=rows[:1000]
        candidates=sorted([r for r in rows if r['geography']!='elsewhere'],key=lambda r:(r['geography']!='dc_dmv',r['identity']))
        byid={identity(r):r for r in candidates}
        for choice in plan_inspections(candidates,detail_budget,location_classifier=market.label if market else geography):
            row=byid[choice['identity']]
            try:jazz_detail(public_text(transport,row['url'],max_bytes=1_000_000,kind='public_html'),row,market)
            except (FetchError,ValueError) as e:
                row['detail_status']=str(e)[:100];stats['errors'].append(str(e)[:100])
                if any(x in str(e) for x in ('403','429','budget','host_stopped')):break
        stats['capped']=stats['capped'] or any(not r['description'] for r in candidates)
    except (FetchError,ValueError) as e:stats['errors'].append(str(e)[:100])
    stats.update(requests=len(transport.requests)-start,unique=len(rows),dc_dmv=sum(r['geography']=='dc_dmv' for r in rows),descriptions=sum(bool(r['description']) for r in rows))
    stats['status']='partial_failure' if rows and stats['errors'] else 'failed' if stats['errors'] else 'ok'
    stats['inventory']={'scope':'one public hosted-career index response','observed_available_items':stats['received'],
        'metadata_retained':len(rows),'metadata_complete':None,'descriptions_observed':stats['descriptions'],
        'description_denominator_observed_records':len(rows),
        'note':'Visible index rows retained. No independently reported total or proof of undisplayed/paginated inventory completeness.'}
    return rows,stats
