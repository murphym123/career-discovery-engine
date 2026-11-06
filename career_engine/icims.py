"""Permitted public iCIMS HTML inventories; no internal API or employer list."""
import hashlib
import re
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit, unquote, urlencode
from urllib.robotparser import RobotFileParser

from career_engine.ats import FetchError, stamp
from career_engine.dc_sources import Document, Node
from career_engine.extract import safe_public_url, USER_AGENT
from career_engine.review_quarantine import ReviewQuarantine
from career_engine.worksite_evidence import worksite_evidence


def public_url(url):
    if not isinstance(url,str):return False
    try:
        u=urlsplit(str(url));host=u.hostname or ''
        if not safe_public_url(url) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,99}\.icims\.com',host):return False
        if host.split('.')[0] in ('www','api','social','developer','login','platform','cdn','cdn01','cdn02'):return False
        if any(unquote(p) in ('.','..') or '/' in unquote(p) or '\\' in unquote(p) for p in u.path.split('/')):return False
        if not (u.path in ('','/','/robots.txt') or re.fullmatch(r'/jobs(?:/(?:search|intro)|/\d+/(?:[^/]+/)?job)?/?',u.path)):return False
        return all(re.fullmatch(r'ss|pr|mode|hashed|search(?:Location|Category|PositionType|Relation|Radius|Zip|Keyword)|mobile|needsRedirect|in_iframe|width|height|bga|jan1offset|jun1offset|schemaId|o',k) and len(v)<=250 for k,v in parse_qsl(u.query))
    except (ValueError,TypeError):return False


def board_url(url):
    if not public_url(url) or urlsplit(url).path=='/robots.txt':return None
    host=urlsplit(url).hostname;token=host.split('.')[0]
    return dict(provider='icims',token=token,key='icims:'+token,url='https://'+host+'/jobs/search')


def posting_id(url):
    if not public_url(url):return ''
    m=re.fullmatch(r'/jobs/(\d+)/(?:[^/]+/)?job/?',urlsplit(url).path)
    return 'icims:'+urlsplit(url).hostname.split('.')[0]+':'+m[1] if m else ''


def public_html(transport,url,max_bytes=1_500_000):
    """Fail closed on unavailable/invalid robots; obey bounded crawl delays."""
    host=urlsplit(url).hostname
    if not public_url(url):raise FetchError('unsafe_icims_endpoint')
    if host not in transport.robots:
        try:
            text=transport.get('https://'+host+'/robots.txt',max_bytes=100000,kind='robots')
            if re.search(r'<(?:html|!doctype)',text,re.I):raise FetchError('invalid_robots_document')
            rule=RobotFileParser();rule.parse(text.lstrip('\ufeff').splitlines());transport.robots[host]=rule
        except FetchError as exc:
            transport.robots[host]=True if str(exc) in ('http_404','http_410') else False
    rule=transport.robots[host]
    if rule is False:raise FetchError('robots_unavailable')
    if rule is not True and not rule.can_fetch(USER_AGENT,url):raise FetchError('robots_disallowed')
    delay=(rule.crawl_delay(USER_AGENT) or 1) if rule is not True else 1
    if delay>10:raise FetchError('stricter_crawl_delay_skip')
    # Transport applies this delay to every request, including redirects.
    text=transport.get(url,max_bytes=max_bytes,kind='public_html')
    if re.search(r'<title[^>]*>\s*(?:Just a moment|.*(?:not a robot|verify.*human|access denied))|cf-chl-|g-recaptcha',text,re.I):
        transport.blocked.add(host);raise FetchError('access_challenge_not_bypassed')
    return text


def header_fields(root):
    values={}
    for n in root.walk():
        if n.tag!='dt':continue
        siblings=n.parent.children;after=siblings[siblings.index(n)+1:]
        data=next((c for c in after if isinstance(c,Node) and c.tag=='dd'),None)
        if data:
            label=re.sub(r'\s*:\s*',' ',n.text().lower()).strip()
            label='location' if label=='location location' else label
            values.setdefault(label,[]).append(data.text())
    return values


def index_page(text,board,url,market=None):
    doc=Document(text).root;rows=[];seen=set();malformed=0
    title=doc.find(lambda n:n.tag=='title');name=title.text() if title else ''
    # "Careers at LOCATION" on detail pages is not a legal-employer name.
    company=re.sub(r'\s*[|–-]\s*(?:Careers Center|Careers|Job Listings|Search Results|Jobs).*$', '',name,flags=re.I).strip()
    if ' | ' in company:
        pieces=[x.strip() for x in company.split('|') if not re.match(r'(?i)jobs|careers|welcome|search',x.strip())]
        company=pieces[0] if pieces else company
    company=re.sub(r'(?i)^Job Listings at |^Jobs at ','',company)
    meta=doc.find(lambda n:n.tag=='meta' and n.attrs.get('name')=='description')
    org=re.search(r'View all current positions at (.+?) across ',meta.attrs.get('content','')) if meta else None
    if org:company=org[1]
    for card in (n for n in doc.walk() if n.has_class('iCIMS_JobCardItem') or n.has_class('iCIMS_JobsTableRow')):
        link=card.find(lambda n:n.tag=='a' and bool(posting_id(urljoin(url,n.attrs.get('href','')))))
        if link is None:malformed+=1;continue
        original=urljoin(url,link.attrs['href']);key=posting_id(original)
        if not key.startswith(board['key']+':'):malformed+=1;continue
        if key in seen:continue
        seen.add(key);u=urlsplit(original);posting=urlunsplit(('https',u.netloc,u.path,'',''))
        fields=header_fields(card);locations=[]
        for label in ('location','job locations','job location','locations','additional locations'):
            locations+=fields.get(label,[])
        for n in card.walk():
            if n.has_class('field-label') and n.text().lower() in ('job locations','location'):
                siblings=n.parent.children;following=[x for x in siblings[siblings.index(n)+1:] if isinstance(x,Node)]
                if following and following[0].tag=='span':locations.append(following[0].text())
        if not locations:
            country=fields.get('job location country',fields.get('country',[]))
            state=fields.get('state',fields.get('job location state',[]));city=fields.get('city',[])
            parts=[v[0] for v in (country,state,city) if v]
            if parts:locations=['-'.join(parts) if country else ', '.join(v[0] for v in (city,state) if v)]
        locations=list(dict.fromkeys(p.strip() for s in locations for p in re.split(r'\s*[|;]\s*',s) if p.strip()))
        name_node=link.find(lambda n:n.tag in ('h2','h3','h4'))
        job_title=name_node.text() if name_node else re.sub(r'^Job Title\s*','',link.text())
        if not job_title:malformed+=1;continue
        row=dict(source='ats_icims',source_id=board['token']+':'+key.rsplit(':',1)[1],identity=key,provider='icims',board_key=board['key'],
            title=job_title,index_fields=fields,company=company or board['token'],company_evidence='public_index_title_label_not_legal_employer_verified' if company else 'board_identifier_only_not_legal_employer_verified',
            index_raw_locations=list(locations),location='; '.join(locations),raw_location='; '.join(locations),raw_locations=locations,url=posting,original_posting_url=original,index_url=url,
            description='',description_sha256='',description_truncated=False,detail_status='index_only',posted_at='',updated_at='',closing_at='',
            work_arrangement='; '.join(fields.get('work arrangement',[])),record_kind='advertised_position',retrieved_at=stamp(),evidence_status='employer_public_index_not_verified_current_paid_or_accepting_applications')
        row.update(worksite_evidence(row,market));rows.append(row)
    visible=doc.text()
    headings=' '.join(n.text() for n in doc.walk() if n.has_class('iCIMS_SubHeader_Jobs') or (n.tag=='h2' and re.match(r'(?i)(?:Search Results|Page \d|Showing )',n.text())))
    page=re.search(r'\bPage\s+(\d+)\s+of\s+(\d+)\b',headings,re.I)
    total=re.search(r'\b(?:of\s+|total(?:\s+of)?\s+)([\d,]+)\s+(?:jobs|results|positions)\b',headings,re.I)
    next_url=''
    for n in doc.walk():
        if n.tag!='a':continue
        if n.attrs.get('aria-disabled')=='true' or 'disabled' in n.attrs or re.search(r'(?i)\b(?:invisible|disabled|iCIMS_NoDisplay)\b',n.attrs.get('class','')):continue
        target=urljoin(url,n.attrs.get('href',''))
        if (n.attrs.get('rel')=='next' or re.search(r'\bnext\b',n.text()+' '+n.attrs.get('aria-label','')+' '+n.attrs.get('title',''),re.I)) and public_url(target) and urlsplit(target).hostname==urlsplit(url).hostname and urlsplit(target).path.rstrip('/')=='/jobs/search':
            next_url=target;break
    empty_message=doc.find(lambda n:(n.has_class('iCIMS_InfoMsg') or n.has_class('iCIMS_NoResults')) and not re.search(r'(?i)hidden|invisible|iCIMS_NoDisplay|display\s*:\s*none',str(n.attrs)))
    empty=not rows and empty_message is not None and bool(re.search(r'\b(?:there are (?:currently )?no (?:available |open )?(?:jobs|positions)|no (?:current )?(?:jobs|positions|results) (?:are |were )?(?:found|available)|0 jobs)\b',empty_message.text(),re.I))
    return rows,dict(page=int(page[1]) if page else None,pages=int(page[2]) if page else None,
        provider_claimed_total=int(total[1].replace(',','')) if total else None,next_url=next_url,explicit_empty=empty,malformed=malformed)


def regional_searches(text,url,market=None):
    doc=Document(text).root;result=[]
    form=doc.find(lambda n:n.tag=='form' and n.attrs.get('id')=='searchForm')
    if form is None or form.attrs.get('method','get').lower()!='get':return []
    action=urljoin(url,form.attrs.get('action',''))
    if not public_url(action) or urlsplit(action).hostname!=urlsplit(url).hostname or urlsplit(action).path.rstrip('/')!='/jobs/search':return []
    from career_engine.markets import DC_MARKET
    market=market or DC_MARKET
    for select in form.walk():
        if select.tag!='select' or select.attrs.get('name')!='searchLocation':continue
        for option in select.walk():
            if option.tag!='option' or not option.attrs.get('value') or market.label(option.text())!=market.local_label:continue
            params=dict(parse_qsl(urlsplit(action).query));params.update(ss='1',searchLocation=option.attrs['value'])
            u=urlsplit(action);target=urlunsplit((u.scheme,u.netloc,u.path,urlencode(params),''))
            if public_url(target):result.append({'url':target,'option_label':option.text(),'option_value':option.attrs['value'],'form_action':action})
    return result


def detail_page(text,row,market=None):
    doc=Document(text).root
    canonical=doc.find(lambda n:n.tag=='link' and n.attrs.get('rel')=='canonical')
    if canonical and posting_id(urljoin(row['url'],canonical.attrs.get('href','')))!=row['identity']:raise ValueError('detail_posting_identity_mismatch')
    fields=header_fields(doc)
    ids=fields.get('job id',fields.get('id',[]))
    if ids and not canonical and not any(re.search(r'(?:^|-)'+re.escape(row['identity'].rsplit(':',1)[1])+r'$',v) for v in ids):raise ValueError('detail_posting_identity_mismatch')
    if not ids and not canonical:raise ValueError('detail_identity_not_established')
    sections=[n for n in doc.walk() if n.has_class('iCIMS_Expandable_Text')]
    parts=[]
    for section in sections:
        if not section.text():continue
        siblings=section.parent.children;prior=siblings[:siblings.index(section)]
        heading=next((n for n in reversed(prior) if isinstance(n,Node) and n.tag in ('h2','h3')),None)
        parts.append((heading.text()+'\n' if heading else '')+section.text())
    body='\n\n'.join(parts)
    if not body:raise ValueError('public_description_section_unavailable')
    # Keep benefits, salary and structured conditions adjacent to the body;
    # do not read candidate forms, login fields or unrelated navigation.
    evidence='\n'.join(k+': '+'; '.join(v) for k,v in fields.items())
    body=(evidence+'\n\n'+body).strip()
    row.update(description=body[:30000],description_sha256=hashlib.sha256(body.encode()).hexdigest(),description_truncated=len(body)>30000,
        detail_status='public_description_observed',description_format='public_iCIMS_dedicated_description_sections',detail_fields=fields)
    locations=[]
    for key in ('job locations','location','additional locations'):locations+=fields.get(key,[])
    if fields.get('city') and fields.get('state'):locations.append(fields['city'][0]+', '+fields['state'][0])
    if locations:
        parts=list(dict.fromkeys(x.strip() for value in locations for x in re.split(r'\s*[|;]\s*',value) if x.strip()))
        row['detail_raw_locations']=list(parts)
        row.update(location='; '.join(parts),raw_location='; '.join(parts),raw_locations=parts)
    row.update(worksite_evidence(row,market));return row


def retrieve_icims(transport,board,max_pages=3,detail_budget=3,market=None):
    start=len(transport.requests);rows=[];seen=set();visited=set();pages=[];url=board['url'];complete=False;available=None;regional_filter=False
    stats=dict(board_key=board['key'],status='ok',errors=[],warnings=[],received=0,malformed=0,unlisted_omitted=0,capped=False,requests=0)
    try:
        for _ in range(max_pages):
            if url in visited:raise FetchError('repeated_pagination_URL')
            visited.add(url);text=public_html(transport,url);batch,info=index_page(text,board,url,market);pages.append({**info,'url':url})
            if regional_filter:
                for row in batch:row['inventory_search_evidence']={**stats['selected_regional_search'],'note':'Publisher filter evidence only; raw displayed locations can differ. Not proof of a local worksite.'}
            stats['received']+=len(batch);stats['malformed']+=info['malformed'];available=info['provider_claimed_total'] or available
            added=0
            for row in batch:
                if row['identity'] not in seen:rows.append(row);seen.add(row['identity']);added+=1
            if len(rows)>=1000:
                stats['capped']=True;stats['errors'].append('index_record_budget');break
            if not batch and not info['explicit_empty']:raise FetchError('index_unresolved_no_public_rows')
            if batch and not added:raise FetchError('repeated_index_page')
            if len(pages)==1 and max_pages>1 and info['pages'] and info['pages']>max_pages:
                options=regional_searches(text,url,market)
                if options:
                    stats['capped']=True
                    stats['observed_regional_search_options']=options
                    stats['warnings'].append('regional_form_window_prioritized_global_pagination_incomplete')
                    cursor=board.get('icims_location_cursor',0)
                    cursor=cursor if isinstance(cursor,int) and cursor>=0 else 0
                    choice=options[cursor%len(options)]
                    board['icims_location_cursor']=(cursor+1)%len(options)
                    stats['selected_regional_search']=choice
                    stats['regional_options_not_attempted']=len(options)-1
                    url=choice['url'];regional_filter=True;continue
            last=info['page'] is not None and info['page']==info['pages']
            if info['explicit_empty'] or last:
                complete=not regional_filter and not info['next_url'] and not stats['malformed'] and (info['explicit_empty'] or [p['page'] for p in pages]==list(range(1,info['page']+1)))
                if regional_filter:stats['regional_window_complete']=last and not info['next_url'] and not info['malformed']
                if complete:available=available if available is not None else len(rows)
                break
            if not info['next_url']:
                stats['warnings'].append('pagination_completeness_unknown');break
            url=info['next_url']
        else:stats['capped']=True;stats['errors'].append('pagination_page_cap')
        if available is not None and complete and len(rows)!=available:complete=False;stats['errors'].append('publisher_total_mismatch')
    except (FetchError,ValueError,RecursionError) as exc:stats['errors'].append(str(exc)[:100])
    quarantine=ReviewQuarantine()
    for row in rows:
        if quarantine.matches(row):row['detail_status']='sealed_review_not_inspected'
    targets=sorted([r for r in rows if r['detail_status']=='index_only' and r['geography']!='elsewhere'],key=lambda r:(r['geography']!='dc_dmv',r['identity']))[:detail_budget]
    for row in targets:
        try:detail_page(public_html(transport,row['url']),row,market)
        except (FetchError,ValueError,RecursionError) as exc:
            row['detail_status']=str(exc)[:100];stats['errors'].append('detail_'+str(exc)[:100])
            if urlsplit(row['url']).hostname in transport.blocked or transport.global_stop_reason():break
    stats.update(requests=len(transport.requests)-start,unique=len(rows),dc_dmv=sum(r['geography']=='dc_dmv' for r in rows),descriptions=sum(bool(r['description']) for r in rows))
    if stats['errors']:stats['status']='partial_failure' if rows else 'failed'
    elif not complete:stats['status']='partial_failure'
    if regional_filter:available=None
    stats['inventory']=dict(scope='permitted public iCIMS search HTML window',observed_available_items=available,provider_claimed_total=next((p['provider_claimed_total'] for p in pages if p['provider_claimed_total'] is not None),None),metadata_complete=complete,
        metadata_retained=len(rows),pagination_pages=pages,descriptions_observed=stats['descriptions'],description_denominator_observed_records=len(rows),verified_empty=complete and not rows)
    return rows,stats
