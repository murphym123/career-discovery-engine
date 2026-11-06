"""Open regional employer evidence and a small, observed public careers-link graph.

A directory headquarters statement is a discovery lead, never a job worksite or
candidate qualification. No company list, invented tenant URLs or search API.
"""
import hashlib
import ipaddress
import re
import socket
from urllib.parse import urlsplit,urlunsplit,urljoin,unquote

from career_engine.ats import PublicTransport,FetchError,board_from_url,stamp
from career_engine.ats_registry import register,observed_ats_urls
from career_engine.dc_sources import Document
from career_engine.extract import safe_public_url
from career_engine.public_sources import public_text

DIRECTORY='https://query.wikidata.org/sparql'
# These are known source-access restrictions, not an employer inclusion list.
RESTRICTED={'pac.org':'https://pac.org/terms-of-use','www.pac.org':'https://pac.org/terms-of-use',
            'www.politicaljobhunt.com':'https://www.politicaljobhunt.com/terms-of-service',
            'www.foundationlist.org':'https://www.foundationlist.org/terms-conditions/',
            'foundationlist.org':'https://www.foundationlist.org/terms-conditions/'}


def website_url(value):
    try:
        u=urlsplit(value)
        if u.scheme=='http':value=urlunsplit(('https',u.netloc,u.path,u.query,''))
        if not safe_public_url(value):return ''
        u=urlsplit(value)
        if any(unquote(part) in ('.','..') or '/' in unquote(part) or '\\' in unquote(part) for part in u.path.split('/')):return ''
        if u.query or re.search(r'(?i)/(?:login|admin|api|wp-admin|account|sign.?in)(?:/|$)',unquote(u.path)):return ''
        return urlunsplit(('https',u.hostname,u.path or '/','',''))
    except (ValueError,TypeError):return ''


class EmployerTransport(PublicTransport):
    """Only exact public URLs discovered in evidence may extend the allowlist."""
    def __init__(self,*a,resolver=socket.getaddrinfo,**kw):
        super().__init__(*a,**kw);self.observed=set();self.resolver=resolver;self.resolved=set()

    def observe(self,url):
        url=website_url(url)
        if not url or any(urlsplit(url).hostname.removeprefix('www.')==h.removeprefix('www.') or urlsplit(url).hostname.endswith('.'+h.removeprefix('www.')) for h in RESTRICTED):return False
        host=urlsplit(url).hostname
        if host not in self.resolved:
            try:
                addresses=self.resolver(host,443,type=socket.SOCK_STREAM)
                if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):return False
            except (OSError,ValueError):return False
            self.resolved.add(host)
        self.observed.update((url,'https://'+host+'/robots.txt'));return True

    def allowed(self,url):return PublicTransport.allowed(url) or url in self.observed

    def redirect_allowed(self,original_host,target):
        if super().redirect_allowed(original_host,target):return True
        # An HTTP redirect is URL evidence, but only a www alias of this same
        # employer may be followed. Never follow another company or ATS/private API.
        host=urlsplit(target).hostname or ''
        if original_host.removeprefix('www.')!=host.removeprefix('www.'):return False
        if not self.observe(target):return False
        return self.allowed(target)


def directory_query(offset=0,limit=200):
    if not 0<=offset<=100000 or not 1<=limit<=500:raise ValueError('Invalid directory bounds')
    # No occupational/ideological/industry exclusions. Dissolved entities are
    # retained as directory leads; only actual live postings count as vacancies.
    return ('SELECT DISTINCT ?org ?orgLabel ?website WHERE { '
            '?org wdt:P159 wd:Q61 ; wdt:P856 ?website . '
            'SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } } '
            f'ORDER BY ?org LIMIT {limit} OFFSET {offset}')


def discover_directory(transport,registry,limit=200):
    offset=registry.get('directory_offset',0);query=directory_query(offset,limit)
    result=transport.json(DIRECTORY,{'query':query,'format':'json'},max_bytes=1_000_000,kind='employer_directory')
    bindings=result.get('results',{}).get('bindings') if isinstance(result,dict) else None
    if not isinstance(bindings,list):raise FetchError('directory_schema')
    registry['directory_offset']=offset+limit if len(bindings)>=limit else 0
    companies=registry.setdefault('employer_sites',{})
    found=0
    for row in bindings:
        if not isinstance(row,dict):continue
        try:
            org=row['org']['value'];name=row['orgLabel']['value'];url=website_url(row['website']['value'])
        except (KeyError,TypeError):continue
        if not re.fullmatch(r'https?://www.wikidata.org/entity/Q\d+',org) or not url:continue
        key=hashlib.sha256(url.encode()).hexdigest()[:24]
        if key not in companies:
            if len(companies)>=5000:break
            companies[key]=dict(url=url,company_label=name,directory_entity=org.replace('http:','https:'),
                evidence_url=DIRECTORY,headquarters_claim='Washington, DC',last_checked_run=None)
            found+=1
    return {'status':'ok','url':DIRECTORY,'query':query,'bindings':len(bindings),'new_employer_sites':found,
            'note':'CC0 crowd-maintained headquarters/website leads, not job-location or current-employer verification.'}


def careers_links(html,base):
    """Only actual links; no guessing /careers or ATS tenant names."""
    links=[]
    for node in Document(html).root.walk():
        if node.tag not in ('a','iframe'):continue
        href=node.attrs.get('href') or node.attrs.get('src') or ''
        url=website_url(urljoin(base,href))
        if not url:continue
        if board_from_url(url) or re.search(r'(?i)\b(careers?|jobs|employment|join (?:our|the) team|work (?:with|for) us)\b',node.text()):
            links.append(url)
    return list(dict.fromkeys(links))


def discover_employer_links(transport,registry,records,budget=8):
    if not 0<=budget<=30:raise ValueError('Employer inspection budget must be 0–30')
    sites=registry.setdefault('employer_sites',{})
    for row in records:
        for url in row.get('employer_site_urls',[]):
            url=website_url(url)
            if not url:continue
            if board_from_url(url):continue  # Already seeded; do not spend employer crawl slots on ATS roots.
            key=hashlib.sha256(url.encode()).hexdigest()[:24]
            if key not in sites and len(sites)<5000:
                sites[key]=dict(url=url,company_label=row.get('company',''),evidence_url=row.get('url',''),
                    source_location=row.get('location',''),last_checked_run=None)
    run=registry.get('run',0)+1
    eligible=[s for s in sites.values() if s.get('last_checked_run') is None or run-s['last_checked_run']>=7]
    eligible.sort(key=lambda s:(s.get('last_checked_run') is not None,hashlib.sha256((str(run)+s['url']).encode()).hexdigest()))
    logs=[]
    for site in eligible[:budget]:
        before=len(transport.requests);count=len(registry['boards']);pages=[]
        log={'url':site['url'],'company_label':site['company_label'],'status':'ok','observed_ATS_boards':0,'pages':pages}
        evidence=dict(kind='regional_employer_public_link',url=site['url'],directory_entity=site.get('directory_entity',''),
            directory_evidence_url=site.get('evidence_url',''),geography_hint='employer_headquarters_only' if site.get('headquarters_claim') else 'source_employer_link_only',
            location=site.get('headquarters_claim') or site.get('source_location',''),
            note='Employer website link observed; headquarters does not establish job location or occupation.')
        try:
            if not hasattr(transport,'observe') or not transport.observe(site['url']):raise FetchError('unapproved_or_nonpublic_employer_URL')
            first=public_text(transport,site['url'],max_bytes=600000,kind='public_html');pages.append(site['url'])
            links=careers_links(first,site['url'])
            # All supported ATS links on the observed page are recorded first.
            for url in links:
                if board_from_url(url):register(registry,url,{**evidence,'observed_url':url})
            host=urlsplit(site['url']).hostname.removeprefix('www.')
            local=[u for u in links if not board_from_url(u) and (urlsplit(u).hostname or '').removeprefix('www.')==host and u!=site['url']]
            for url in local[:2]:
                if not transport.observe(url):continue
                html=public_text(transport,url,max_bytes=600000,kind='public_html');pages.append(url)
                for observed in observed_ats_urls(html):register(registry,observed,{**evidence,'url':url,'observed_url':observed})
            log['observed_ATS_boards']=len(registry['boards'])-count
            if not links:log['status']='no_careers_link_observed'
        except (FetchError,ValueError,RecursionError) as e:log['status']=str(e)[:100]
        site['last_checked_run']=run;site['last_status']=log['status'];log['requests']=len(transport.requests)-before
        logs.append(log)
        if any(x in log['status'] for x in ('request_budget','byte_budget','time_budget')):break
    return logs
