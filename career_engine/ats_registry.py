"""Evidence-backed employer-board discovery, with no compiled employer corpus.

Sources: URLs in unranked source evidence, optional evidence imports, and bounded
Common Crawl URL-index queries. Archive URLs are seeds, never live-job evidence.
"""
import hashlib
import json
import re
from collections import defaultdict,deque
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlsplit
from datetime import datetime,timezone

from career_engine.ats import board_from_url, stamp, FetchError
from career_engine.extract import safe_public_url

PREFIXES=('*','council*','research*','public*','foundation*','american*','consult*','association*',
          'institute*','policy*','communications*','university*','advocacy*','strategy*',
          *tuple(chr(c)+'*' for c in range(ord('a'),ord('z')+1)))
INDEX_HOSTS=(('greenhouse','job-boards.greenhouse.io'),('lever','jobs.lever.co'),('ashby','jobs.ashbyhq.com'))
UNSUPPORTED=('myworkdayjobs.com','icims.com','bamboohr.com','smartrecruiters.com','rippling.com',
             'rippling-ats.com','paycomonline.net','oraclecloud.com','adp.com','paylocity.com')


class _Links(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.urls=[]
    def handle_starttag(self,tag,attrs):
        for key,value in attrs:
            if key in ('href','src') and value:self.urls.append(value)


def observed_ats_urls(text):
    """Parse URL evidence, not company guesses. No supplied URL is requested."""
    text=unescape(str(text or '')).replace('\\/','/')
    parser=_Links()
    try:parser.feed(text)
    except (ValueError,RecursionError):pass
    values=parser.urls+re.findall(r'https?://[^\s<>"\x27\\]+',text)
    valid=set()
    for u in values:
        u=u.rstrip('.,);]')
        if board_from_url(u) or (safe_public_url(u) and any((urlsplit(u).hostname or '').endswith('.'+h) or urlsplit(u).hostname==h for h in UNSUPPORTED)):
            valid.add(u)
    return sorted(valid)


def empty_registry():return {'schema_version':1,'run':0,'query_offset':0,'index_pages':{},'index_counts':{},'host_not_before':{},'boards':{},'unsupported':[]}


def validate_registry(data):
    if not isinstance(data,dict) or data.get('schema_version')!=1 or not isinstance(data.get('boards'),dict):raise ValueError('Invalid ATS registry')
    result=empty_registry()
    result.update({k:data.get(k,result[k]) for k in ('run','query_offset','index_pages','index_counts','host_not_before','unsupported')})
    if not isinstance(result['run'],int) or not isinstance(result['query_offset'],int) or result['run']<0 or result['query_offset']<0:raise ValueError('Invalid registry cursor')
    if not isinstance(result['index_counts'],dict) or not isinstance(result['host_not_before'],dict):raise ValueError('Invalid index/cooldown metadata')
    if not isinstance(result['index_pages'],dict):raise ValueError('Invalid index cursor')
    result['unsupported']=result['unsupported'][:500] if isinstance(result['unsupported'],list) else []
    for key,b in list(data['boards'].items())[:5000]:
        if not isinstance(b,dict):continue
        parsed=board_from_url(b.get('url'))
        if parsed and parsed['key']==key:
            result['boards'][key]={**b,**parsed,'evidence':b.get('evidence',[])[:20] if isinstance(b.get('evidence'),list) else []}
    return result


def register(registry,url,evidence):
    board=board_from_url(url)
    if not board:
        if safe_public_url(str(url)) and len(registry['unsupported'])<500:
            record={'url':url,'evidence':evidence,'status':'unsupported_platform_not_requested'}
            if not any(r.get('url')==url for r in registry['unsupported']):registry['unsupported'].append(record)
        return False
    if board['key'] not in registry['boards']:
        if len(registry['boards'])>=5000:return False
        registry['boards'][board['key']]={**board,'first_seen_at':stamp(),'evidence':[],'last_checked_run':None}
    entry=registry['boards'][board['key']]
    if evidence not in entry['evidence'] and len(entry['evidence'])<20:entry['evidence'].append(evidence)
    entry['last_discovered_at']=stamp()
    return True


def source_seeds(registry,records):
    added=0
    for row in records:
        urls=set(observed_ats_urls(row.get('description','')))
        for field in ('url','employer_board_urls'):
            value=row.get(field,[])
            urls.update(value if isinstance(value,list) else observed_ats_urls(value))
        for url in urls:
            before=len(registry['boards'])
            register(registry,url,{'kind':'source_audit_link','url':row.get('url',''),'source':row.get('source',''),
                                  'source_id':row.get('source_id',''),'geography_hint':row.get('geography','unknown')})
            added+=len(registry['boards'])-before
    return added


def seed_plan(offset,count):
    result=[]
    for n in range(offset,offset+count):
        provider,host=INDEX_HOSTS[n%len(INDEX_HOSTS)]
        prefix=PREFIXES[(n//len(INDEX_HOSTS))%len(PREFIXES)]
        # Periodically include the older Greenhouse hostname without adding any
        # employer-specific patterns. Both lead to the same public API.
        if provider=='greenhouse' and (n//(len(INDEX_HOSTS)*len(PREFIXES)))%2:host='boards.greenhouse.io'
        result.append({'provider':provider,'pattern':host+'/'+prefix,'family':'unrestricted' if prefix=='*' else 'employer_prefix:'+prefix})
    return result


def archive_fallback(transport,registry,query,page):
    endpoint='https://web.archive.org/cdx/search/cdx'
    params={'url':query['pattern'],'output':'json','filter':'statuscode:200','collapse':'urlkey',
            'fl':'original,timestamp','limit':1000,'from':str(datetime.now(timezone.utc).year-2),
            'pageSize':1}
    pagekey='internet_archive|'+params['from']+'|'+query['pattern']
    actual_page=registry['index_pages'].get(pagekey);page_count_known=pagekey in registry['index_counts']
    if actual_page is None and query['family']=='unrestricted':
        counts=transport.json(endpoint,{**params,'showNumPages':'true'},max_bytes=10000,kind='seed_page_count')
        page_count_known=isinstance(counts,dict) and isinstance(counts.get('pages'),int) and 1<=counts['pages']<=100000
        total=counts.get('pages',1) if isinstance(counts,dict) else 1
        if not isinstance(total,int) or not 1<=total<=100000:total=1
        if page_count_known:registry['index_counts'][pagekey]=total
        actual_page=int(hashlib.sha256(pagekey.encode()).hexdigest()[:12],16)%total
    if not isinstance(actual_page,int) or not 0<=actual_page<=100000:actual_page=0
    total=registry['index_counts'].get(pagekey)
    if isinstance(total,int) and total>0:actual_page%=total
    params['page']=actual_page
    try:data=transport.json(endpoint,params,max_bytes=1_000_000,kind='seed_index_fallback')
    except FetchError as e:
        if str(e) in ('http_400','http_404'):registry['index_pages'][pagekey]=0
        raise
    if not isinstance(data,list):raise FetchError('fallback_schema')
    before=len(registry['boards']);lines=0
    for record in data[1:]:
        if not isinstance(record,list) or not record:continue
        board=board_from_url(record[0])
        if not board or board['provider'].removesuffix('_eu')!=query['provider']:continue
        lines+=1
        register(registry,record[0],{'kind':'internet_archive_url_index','url':endpoint,'pattern':query['pattern'],
                'page':actual_page,'family':query['family'],'archive_timestamp':str(record[1] if len(record)>1 else ''),
                'observed_url':record[0]})
    registry['index_pages'][pagekey]=actual_page+1
    return {'status':'ok','page':actual_page,'index_lines':lines,'new_boards':len(registry['boards'])-before,
            'limit':1000,'capped':len(data)>1000 or (isinstance(total,int) and total>1),
            'page_count_known':page_count_known,
            'note':'Bounded URL evidence; unavailable page counts leave initial-page alphabetical bias.'}


def discover_commoncrawl(transport,registry,query_budget=6):
    if not 0<=query_budget<=20:raise ValueError('Seed query budget must be 0–20')
    logs=[]
    if not query_budget:return logs
    try:
        catalog=transport.json('https://index.commoncrawl.org/collinfo.json',max_bytes=200000,kind='seed_catalog')
        choices=[r['id'] for r in catalog if isinstance(r,dict) and re.fullmatch(r'CC-MAIN-\d{4}-\d{2}',str(r.get('id','')))]
        if not choices:raise FetchError('catalog_schema')
        collection=sorted(choices,reverse=True)[0]
    except (FetchError,TypeError,KeyError) as e:return [{'status':str(e),'kind':'catalog_unavailable','new_boards':0}]
    plan=seed_plan(registry['query_offset'],query_budget)
    for query in plan:
        registry['query_offset']+=1
        pagekey=collection+'|'+query['pattern'];page=registry['index_pages'].get(pagekey)
        if page is not None and (not isinstance(page,int) or not 0<=page<=100000):page=0
        log={**query,'collection':collection,'page':page,'status':'ok','index_lines':0,'new_boards':0,
             'limit':2500,'board_URLs':0,'note':'Historical URL evidence only; no assertion of current employer/job availability.'}
        try:
            endpoint='https://index.commoncrawl.org/'+collection+'-index'
            params={'url':query['pattern'],'output':'json','filter':'status:200','fl':'url,timestamp,status',
                    'limit':2500,'pageSize':1}
            if page is None and query['family']=='unrestricted':
                info=transport.json(endpoint,{**params,'showNumPages':'true'},max_bytes=10000,kind='seed_page_count')
                count_known=isinstance(info,dict) and isinstance(info.get('pages'),int) and 1<=info['pages']<=100000
                total=info.get('pages',1) if isinstance(info,dict) else 1
                if not isinstance(total,int) or not 1<=total<=100000:total=1
                if count_known:registry['index_counts'][pagekey]=total
                page=int(hashlib.sha256(pagekey.encode()).hexdigest()[:12],16)%total
            if page is None:page=0
            total=registry['index_counts'].get(pagekey)
            if isinstance(total,int) and total>0:page%=total
            params['page']=page;log['page']=page
            body=transport.get(endpoint,params,max_bytes=1_500_000,kind='seed_index')
            lines=body.splitlines();log['capped']=len(lines)>=2500 or (isinstance(total,int) and total>1)
            for line in lines:
                try:item=json.loads(line)
                except ValueError:continue
                if not isinstance(item,dict) or not item.get('url'):continue
                log['index_lines']+=1;before=len(registry['boards'])
                board=board_from_url(item['url'])
                if not board or (board['provider'].removesuffix('_eu')!=query['provider']):continue
                log['board_URLs']+=1
                register(registry,item['url'],{'kind':'commoncrawl_url_index','url':endpoint,'pattern':query['pattern'],
                         'page':page,'family':query['family'],'archive_timestamp':str(item.get('timestamp','')),
                         'observed_url':item['url']})
                log['new_boards']+=len(registry['boards'])-before
            registry['index_pages'][pagekey]=page+1
            if not log['board_URLs']:
                log['status']='no_public_board_URLs_in_index_sample'
                try:
                    fallback=archive_fallback(transport,registry,query,page);log['fallback']=fallback
                    log['new_boards']+=fallback['new_boards']
                except FetchError as failure:log['fallback']={'status':str(failure),'new_boards':0}
        except FetchError as e:
            log['status']=str(e)
            if str(e) not in ('http_403','http_429','host_stopped_after_denial','request_budget','byte_budget'):
                try:
                    fallback=archive_fallback(transport,registry,query,page)
                    log['fallback']=fallback
                    log['new_boards']+=fallback['new_boards']
                except FetchError as failure:log['fallback']={'status':str(failure),'new_boards':0}
            if str(e) in ('http_400','http_404') and log.get('fallback',{}).get('status')!='ok':registry['index_pages'][pagekey]=0
        logs.append(log)
        if 'index.commoncrawl.org' in transport.blocked:break
    return logs


def select_boards(registry,budget=30):
    if not 1<=budget<=100:raise ValueError('Board budget must be 1–100')
    registry['run']+=1;run=registry['run']
    new=[];known=[]
    for entry in registry['boards'].values():
        # Manual runs only; repeated known failures get a run-based cool-down.
        last=entry.get('last_checked_run')
        if last is None:new.append(entry)
        elif isinstance(last,int) and run-last>= (7 if entry.get('status')=='failed' else 1):known.append(entry)
    def balanced(values):
        groups=defaultdict(list)
        for e in values:groups[e['provider']].append(e)
        for key,group in list(groups.items()):
            groups[key]=deque(sorted(group,key=lambda e:(not any(x.get('kind')=='source_audit_link' for x in e['evidence']),
                               not bool(e.get('dc_dmv')), e.get('last_checked_run') or 0,
                               -max([int(str(x.get('archive_timestamp',''))[:8]) for x in e['evidence'] if str(x.get('archive_timestamp',''))[:8].isdigit()] or [0]),hashlib.sha256(e['key'].encode()).hexdigest())))
        result=[]
        while groups:
            for key in sorted(groups):
                result.append(groups[key].popleft())
                if not groups[key]:del groups[key]
        return result
    # Broad unknown-board discovery stays primary; refresh known boards second.
    fresh=balanced(new);refresh=balanced(known);new_slots=max(1,(budget*7+9)//10)
    selected=fresh[:new_slots]+refresh[:budget-new_slots]
    used={e['key'] for e in selected}
    selected += [e for e in fresh+refresh if e['key'] not in used][:budget-len(selected)]
    return selected[:budget]
