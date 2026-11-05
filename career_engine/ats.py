"""Bounded GET-only adapters for documented public employer ATS boards.

No employer names, credentials, search services or application submission paths.
Board IDs must come from a recorded public URL, never guessed company names.
"""
import hashlib
import html
import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import requests
from career_engine.extract import safe_public_url, strip_html, USER_AGENT
from career_engine.discovery import geography

PROVIDERS=('greenhouse','lever','ashby')
PUBLIC_HOSTS={'job-boards.greenhouse.io':'greenhouse','boards.greenhouse.io':'greenhouse',
              'jobs.lever.co':'lever','jobs.eu.lever.co':'lever_eu','jobs.ashbyhq.com':'ashby'}
API_HOSTS={'boards-api.greenhouse.io','api.lever.co','api.eu.lever.co','api.ashbyhq.com'}
MAX_DESCRIPTION=30000


def stamp():return datetime.now(timezone.utc).isoformat(timespec='seconds')


def board_from_url(url):
    """Parse observed public/API URLs; no network request to the supplied URL.

    Historical HTTP seed URLs may be upgraded; every actual request is HTTPS.
    Reject credentials, ports, encoded path separators and API/private shortcuts.
    """
    try:
        u=urlsplit(str(url));host=(u.hostname or '').lower()
        if u.scheme not in ('http','https') or u.username or u.password or u.port not in (None,443):return None
        parts=[unquote(x) for x in u.path.strip('/').split('/')]
        provider=PUBLIC_HOSTS.get(host);token=parts[0] if parts else ''
        if host=='boards-api.greenhouse.io' and parts[:2]==['v1','boards'] and len(parts)>=3:
            provider,token='greenhouse',parts[2]
        elif host in ('api.lever.co','api.eu.lever.co') and parts[:2]==['v0','postings'] and len(parts)>=3:
            provider,token=('lever_eu' if host=='api.eu.lever.co' else 'lever'),parts[2]
        elif host=='api.ashbyhq.com' and parts[:2]==['posting-api','job-board'] and len(parts)>=3:
            provider,token='ashby',parts[2]
        if not provider or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._-]{0,99}',token):return None
        if token.lower() in ('api','meeting','b','embed','users','login','sitemap.xml','robots.txt'):return None
        return {'provider':provider,'token':token,'key':provider+':'+token.lower(),'url':public_board_url(provider,token)}
    except (ValueError,TypeError):return None


def public_board_url(provider,token):
    host={'greenhouse':'job-boards.greenhouse.io','lever':'jobs.lever.co',
          'lever_eu':'jobs.eu.lever.co','ashby':'jobs.ashbyhq.com'}[provider]
    return 'https://'+host+'/'+quote(token,safe='._-')


def posting_key(url):
    b=board_from_url(url)
    if not b:return ''
    parts=[unquote(x) for x in urlsplit(url).path.strip('/').split('/')]
    if b['provider']=='greenhouse':
        if len(parts)>=3 and parts[1]=='jobs' and re.fullmatch(r'\d+',parts[2]):return b['key']+':'+parts[2]
    elif len(parts)>=2 and re.fullmatch(r'[a-zA-Z0-9-]{8,80}',parts[1]):
        return b['key']+':'+parts[1].lower()
    return ''


def canonical_posting_url(url):
    key=posting_key(url)
    if key:
        b=board_from_url(url);post=key.rsplit(':',1)[1]
        return b['url']+('/jobs/' if b['provider']=='greenhouse' else '/')+post
    if not safe_public_url(str(url)):return ''
    u=urlsplit(url)
    # Preserve meaningful job query IDs on custom employer landing pages.
    return urlunsplit(('https',u.hostname.lower(),u.path.rstrip('/') or '/',u.query,''))


class FetchError(RuntimeError):pass


class PublicTransport:
    """Fixed public endpoint allowlist, global limits, denials stop a host.

    Published API GETs are used according to provider docs. No application, admin,
    authenticated, GraphQL, tenant-guessing, archive replay or paid endpoints.
    """
    def __init__(self,session=None,max_requests=180,pause=time.sleep,max_bytes=64_000_000,max_seconds=600):
        self.session=session if session is not None else requests.Session()
        self.max_requests=max_requests;self.pause=pause;self.max_bytes=max_bytes
        self.started=time.monotonic();self.max_seconds=max_seconds
        self.requests=[];self.bytes=0;self.blocked=set();self.robots={}

    @staticmethod
    def allowed(url):
        if not safe_public_url(url):return False
        u=urlsplit(url);p=u.path
        if any(unquote(seg) in ('.','..') or '/' in unquote(seg) or '\\' in unquote(seg) for seg in p.split('/')):return False
        token=r'[A-Za-z0-9%._-]+'
        if u.hostname=='boards-api.greenhouse.io':return bool(re.fullmatch(r'/v1/boards/'+token+r'(?:/jobs(?:/\d+)?)?',p))
        if u.hostname in ('api.lever.co','api.eu.lever.co'):return bool(re.fullmatch(r'/v0/postings/'+token,p))
        if u.hostname=='api.ashbyhq.com':return bool(re.fullmatch(r'/posting-api/job-board/'+token,p))
        if u.hostname in PUBLIC_HOSTS:
            return p=='/robots.txt' or (board_from_url(url) is not None and len(p.strip('/').split('/'))==1)
        if u.hostname=='web.archive.org':return p=='/cdx/search/cdx'
        if u.hostname=='index.commoncrawl.org':return p=='/collinfo.json' or bool(re.fullmatch(r'/CC-MAIN-\d{4}-\d{2}-index',p))
        return False

    def get(self,url,params=None,max_bytes=8_000_000,kind='api'):
        if not self.allowed(url):raise FetchError('unsafe_endpoint')
        original_host=urlsplit(url).hostname
        if original_host in self.blocked:raise FetchError('host_stopped_after_denial')
        for redirect in range(3):
            if time.monotonic()-self.started>self.max_seconds:raise FetchError('time_budget')
            if len(self.requests)>=self.max_requests:raise FetchError('request_budget')
            if self.bytes>=self.max_bytes:raise FetchError('byte_budget')
            self.pause(1)
            item={'url':url,'params':params or {},'kind':kind,'observed_at':stamp()};self.requests.append(item)
            try:
                r=self.session.get(url,params=params,headers={'User-Agent':USER_AGENT,'Accept':'application/json'},
                                   timeout=20,allow_redirects=False,stream=True)
            except requests.RequestException:
                item['status']='network_error';raise FetchError('network_error') from None
            try:
                item['status']=r.status_code
                if r.status_code in (403,429):
                    self.blocked.add(original_host);item['retry_after']=str(r.headers.get('Retry-After',''))[:80]
                if r.status_code in (301,302,303,307,308):
                    from urllib.parse import urljoin
                    target=urljoin(url,r.headers.get('Location',''))
                    if not self.allowed(target) or urlsplit(target).hostname!=original_host:raise FetchError('unsafe_redirect')
                    url=target;params=None;continue
                if r.status_code!=200:raise FetchError('http_'+str(r.status_code))
                content_type=r.headers.get('Content-Type','').split(';')[0].strip().lower()
                if content_type and not any(x in content_type for x in (('text/html','text/plain') if kind in ('public_html','robots') else ('json','text/plain','octet-stream'))):raise FetchError('unexpected_content_type')
                data=bytearray()
                for chunk in r.iter_content(65536):
                    self.bytes+=len(chunk);data.extend(chunk)
                    if time.monotonic()-self.started>self.max_seconds:raise FetchError('time_budget')
                    if len(data)>max_bytes or self.bytes>self.max_bytes:raise FetchError('response_or_byte_limit')
                item.update(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
                try:return bytes(data).decode('utf-8')
                except UnicodeError:raise FetchError('invalid_encoding') from None
            except requests.RequestException:
                item['error']='stream_error';raise FetchError('stream_error') from None
            except FetchError as error:
                item['error']=str(error);raise
            finally:r.close()
        raise FetchError('redirect_limit')

    def json(self,url,params=None,**kw):
        try:return json.loads(self.get(url,params,**kw))
        except (ValueError,UnicodeError):raise FetchError('invalid_json') from None


def board_name(transport,board):
    from urllib.robotparser import RobotFileParser
    from html.parser import HTMLParser
    url=board['url'];host=urlsplit(url).hostname
    if host not in transport.robots:
        try:
            text=transport.get('https://'+host+'/robots.txt',max_bytes=100000,kind='robots')
            rp=RobotFileParser();rp.parse(text.splitlines());transport.robots[host]=rp
        except FetchError as e:
            transport.robots[host]=True if str(e)=='http_404' else False
    rp=transport.robots[host]
    if rp is False or (rp is not True and not rp.can_fetch(USER_AGENT,url)):return ''
    delay=rp.crawl_delay(USER_AGENT) if rp is not True else None
    if delay and delay>1: return ''  # Metadata is optional; do not exceed a stricter site pacing rule.
    try:text=transport.get(url,max_bytes=600000,kind='public_html')
    except FetchError:return ''
    class Title(HTMLParser):
        def __init__(self):super().__init__(convert_charrefs=True);self.active=False;self.parts=[];self.og=''
        def handle_starttag(self,tag,attrs):
            self.active=self.active or tag=='title'
            a=dict(attrs)
            if tag=='meta' and a.get('property')=='og:title':self.og=a.get('content','')
        def handle_endtag(self,tag):
            if tag=='title':self.active=False
        def handle_data(self,data):
            if self.active:self.parts.append(data)
    p=Title();p.feed(text);title=p.og or ' '.join(p.parts)
    title=re.sub(r'^(?:Jobs at|Careers at|Join)\s+','',title,flags=re.I)
    title=re.sub(r'\s*(?:[-|–]\s*)?(?:Jobs|Careers|Job Openings)(?:\s*[-|–]\s*(?:Ashby|Lever))?\s*$','',title,flags=re.I).strip()
    return title[:200] if title and title.lower() not in ('ashby','lever','job openings','careers') else ''


def _location(value):
    if isinstance(value,str):return value
    if isinstance(value,dict):return str(value.get('name') or value.get('location') or '')
    return ''


def _description(value):return strip_html(html.unescape(str(value or '')))


def normalize_posting(board,item,company=''):
    if not isinstance(item,dict):raise ValueError('posting not an object')
    provider=board['provider'];token=board['token'];raw=[]
    if provider=='greenhouse':
        post=str(item.get('id',''));title=item.get('title','');url=item.get('absolute_url','')
        raw=[_location(item.get('location'))];body=_description(item.get('content'))
        company=item.get('company_name') or company;published=item.get('first_published','')
        updated=item.get('updated_at','');closing=item.get('application_deadline','') or ''
        kind='prospect_pool' if 'internal_job_id' in item and item['internal_job_id'] is None else 'advertised_position'
        employment='';workplace='';dept='; '.join(str(d.get('name','')) for d in item.get('departments',[]) if isinstance(d,dict))
    elif provider.startswith('lever'):
        post=str(item.get('id',''));title=item.get('text','');url=item.get('hostedUrl','')
        c=item.get('categories') or {};raw=[_location(c.get('location'))]+[_location(x) for x in c.get('allLocations',[]) or []]
        body=_description(item.get('descriptionPlain') or item.get('description'))
        for group in item.get('lists',[]) or []:
            if isinstance(group,dict):body+='\n'+str(group.get('text',''))+'\n'+_description(group.get('content'))
        body+='\n'+_description(item.get('additionalPlain') or item.get('additional'))
        published='';updated='';closing='';kind='advertised_position';employment=c.get('commitment','');workplace=item.get('workplaceType','');dept=c.get('department') or c.get('team','')
    elif provider=='ashby':
        post=str((item.get('jobUrl') or '').rstrip('/').rsplit('/',1)[-1])
        title=item.get('title','');url=item.get('jobUrl','');raw=[_location(item.get('location'))]
        raw += [_location(x) for x in item.get('secondaryLocations',[]) or []]
        body=str(item.get('descriptionPlain') or _description(item.get('descriptionHtml')))
        published=item.get('publishedAt','');updated='';closing='';kind='advertised_position';employment=item.get('employmentType','');workplace=item.get('workplaceType','');dept=item.get('department','')
    else:raise ValueError('unsupported provider')
    if provider=='greenhouse' and not post.isdigit():raise ValueError('invalid Greenhouse posting ID')
    if not title or not post or not re.fullmatch(r'[A-Za-z0-9-]{1,100}',post):raise ValueError('missing title or posting ID')
    raw=list(dict.fromkeys(x for x in raw if x));location=' ; '.join(raw)
    if str(workplace).lower()=='remote' and not re.search(r'\bremote\b',location,re.I):location='Remote; '+(location or 'eligibility unknown')
    original_url=url
    url=canonical_posting_url(url)
    url_key=posting_key(url) if url else ''
    if url_key and (not url_key.startswith(board['key']+':') or url_key.rsplit(':',1)[-1]!=post.lower()):raise ValueError('posting URL board/ID mismatch')
    if not url:raise ValueError('unsafe or absent posting URL')
    identity=board['key']+':'+post
    return dict(source='ats_'+provider,source_id=token+':'+post,identity=identity,provider=provider,board_key=board['key'],
                title=str(title),company=str(company or token),company_evidence='provider_company_or_board_name' if company else 'board_identifier_only_not_legal_employer_verified',
                location=location,raw_locations=raw,raw_location=location,geography=geography(location),url=url,original_posting_url=original_url,
                description=body[:MAX_DESCRIPTION],description_truncated=len(body)>MAX_DESCRIPTION,
                description_sha256=hashlib.sha256(body.encode()).hexdigest() if body else '',posted_at=published,updated_at=updated,closing_at=closing,
                employment_type=employment,work_arrangement=workplace,category=dept,record_kind=kind,
                index_url=board['url'],retrieved_at=stamp(),detail_status='public_description_observed' if body else 'index_only',
                evidence_status='employer_managed_public_ATS_record_observed_not_independently_verified_accepting_applications')


def retrieve_board(transport,board,max_pages=3,detail_budget=3):
    parsed=board_from_url(board.get('url',''))
    if not parsed or parsed['key']!=board.get('key') or parsed['provider']!=board.get('provider') or parsed['token']!=board.get('token'):raise ValueError('Invalid observed board')
    if not 1<=max_pages<=10 or not 0<=detail_budget<=30:raise ValueError('Invalid ATS page/detail cap')
    provider=board['provider'];token=quote(board['token'],safe='._-')
    stats={'board_key':board['key'],'status':'ok','errors':[],'warnings':[],'received':0,'malformed':0,'unlisted_omitted':0,'capped':False,'requests':0}
    start=len(transport.requests);rows=[];company=''
    try:
        if provider=='greenhouse':
            base='https://boards-api.greenhouse.io/v1/boards/'+token
            data=transport.json(base+'/jobs')
            items=data.get('jobs') if isinstance(data,dict) else None
            if not isinstance(items,list):raise FetchError('invalid_schema')
            try:
                info=transport.json(base);company=str(info.get('name') or '') if isinstance(info,dict) else ''
            except FetchError as e:
                stats['warnings'].append('board_name_'+str(e))
                if any(x in str(e) for x in ('403','429','host_stopped')):stats['errors'].append(str(e))
            batches=[items];stats['capped']=len(items)>5000
        elif provider=='ashby':
            data=transport.json('https://api.ashbyhq.com/posting-api/job-board/'+token,{'includeCompensation':'true'})
            items=data.get('jobs') if isinstance(data,dict) else None
            if not isinstance(items,list):raise FetchError('invalid_schema')
            batches=[items];stats['capped']=len(items)>5000
        else:batches=None
        seen=set()
        for page in range(max_pages if provider.startswith('lever') else 1):
            if provider.startswith('lever'):
                host='api.eu.lever.co' if provider=='lever_eu' else 'api.lever.co'
                items=transport.json('https://'+host+'/v0/postings/'+token,{'mode':'json','limit':100,'skip':page*100})
                if not isinstance(items,list):raise FetchError('invalid_schema')
            else:items=batches[0][:5000]
            added=0
            for item in items:
                if provider=='ashby' and isinstance(item,dict) and item.get('isListed') is False:
                    stats['unlisted_omitted']+=1;continue
                stats['received']+=1
                try:row=normalize_posting(board,item,company)
                except (ValueError,TypeError,AttributeError):stats['malformed']+=1;continue
                if row['identity'] not in seen:rows.append(row);seen.add(row['identity']);added+=1
            if provider.startswith('lever'):
                if len(items)<100:break
                if not added:stats['errors'].append('repeated_page');break
                if page==max_pages-1:stats['capped']=True
        if rows and not company and provider!='greenhouse':
            company=board_name(transport,board)
            if company:
                for row in rows:
                    row['company']=company;row['company_evidence']='public_board_page_title'
        if provider=='greenhouse':
            # Metadata-first avoids downloading thousands of global descriptions.
            # Only geography guides detail priority; no fit scores or title gates.
            targets=sorted(rows,key=lambda r:({'dc_dmv':0,'us_remote':1,'regional_worksite_unknown':2,'unknown':3,'remote_eligibility_unknown':3,'elsewhere':4}[r['geography']],hashlib.sha256(r['url'].encode()).hexdigest()))
            targets=[r for r in targets if r['geography']!='elsewhere'][:detail_budget]
            for r in targets:
                try:
                    detail=transport.json(base+'/jobs/'+r['identity'].rsplit(':',1)[-1]);new=normalize_posting(board,detail,company)
                    if new['identity']!=r['identity']:raise ValueError('detail posting ID mismatch')
                    r.update(new)
                except (FetchError,ValueError,TypeError) as e:
                    r['detail_status']=str(e)[:80];stats['errors'].append('detail_'+str(e)[:80])
                    if isinstance(e,FetchError) and any(x in str(e) for x in ('403','429','budget','host_stopped')):break
            stats['capped']=stats['capped'] or sum(bool(r['description']) for r in rows)<sum(r['geography']!='elsewhere' for r in rows)
    except (FetchError,ValueError,TypeError,AttributeError) as e:stats['errors'].append(str(e)[:100])
    stats['requests']=len(transport.requests)-start;stats['unique']=len(rows)
    stats['dc_dmv']=sum(r['geography']=='dc_dmv' for r in rows);stats['descriptions']=sum(bool(r['description']) for r in rows)
    stats['status']='partial_failure' if rows and stats['errors'] else 'failed' if stats['errors'] else 'ok'
    return rows,stats


def enrich_regional_metadata(transport,records,boards,budget=20,seed=0):
    """A shared post-discovery detail budget; never remove an index record.

    Reuses broad inspection strata (including atypical exploration), independent
    of candidate-fit ranking. Poll all boards first so one large employer cannot
    spend the registry-discovery budget on its own descriptions.
    """
    from career_engine.inspection import plan_inspections,identity as inspection_identity
    if not 0<=budget<=30:raise ValueError('Extra detail budget must be 0–30')
    candidates=[r for r in records if r['provider']=='greenhouse' and not r.get('description')
                and r.get('detail_status')=='index_only' and r['geography']!='elsewhere']
    candidates.sort(key=lambda r:(r.get('updated_at',''),r['identity']),reverse=True)
    plan=plan_inspections(candidates,budget,seed)
    available={inspection_identity(r):r for r in candidates};log=[]
    for choice in plan:
        r=available[choice['identity']];board=boards[r['board_key']]
        endpoint='https://boards-api.greenhouse.io/v1/boards/'+quote(board['token'],safe='._-')+'/jobs/'+r['identity'].rsplit(':',1)[-1]
        before=len(transport.requests)
        item={'identity':r['identity'],'board_key':r['board_key'],'stratum':choice['stratum'],'status':'ok'}
        try:
            data=transport.json(endpoint)
            company=r['company'] if r.get('company_evidence')!='board_identifier_only_not_legal_employer_verified' else ''
            fields=normalize_posting(board,data,company)
            if fields['identity']!=r['identity']:raise ValueError('detail posting ID mismatch')
            r.update(fields);r['detail_inspection_stratum']=choice['stratum']
        except (FetchError,ValueError,TypeError) as e:
            item['status']=str(e)[:100];r['detail_status']=item['status']
        item['requests']=len(transport.requests)-before;log.append(item)
        if any(x in item['status'] for x in ('403','429','budget','host_stopped')):break
    return log
