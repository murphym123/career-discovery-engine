"""Published RSS and sitemap discovery, never paid search or employer guesses."""
import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree as ET

from career_engine.ats import FetchError, board_from_url, stamp
from career_engine.ats_registry import register, observed_ats_urls
from career_engine.discovery import geography
from career_engine.extract import USER_AGENT, strip_html,safe_public_url

IMPACT_FEED='https://impactopportunity.org/rss/'
JAZZ_SITEMAP='https://app.jazz.co/feeds/google/xml/'


def url_location_hint(slug,market=None):
    text=slug.replace('-',' ').replace('_',' ')
    label=market.label(text) if market else geography(text)
    if market and market.identifier!='dc-dmv' and label!=market.local_label and re.search(r'(?i)\b'+re.escape(market.city)+r'(?:\s+(?:office|opportunities))?\s*$',text):
        return 'possible_market'  # A city-only URL suffix is routing evidence, never a verified worksite.
    # Postal DC at a URL's location suffix, including multi-office NYC-and-DC
    # postings, is a useful *seed hint*. It never verifies a live job's location.
    if (not market or market.identifier=='dc-dmv') and label!='dc_dmv' and re.search(r'(?i)\bDC(?:\s+(?:office|metro|opportunities))?\s*$',text):return 'dc_dmv'
    return label


def public_text(transport,url,**kwargs):
    """Robots-aware public documents; failed rules/challenges are not bypassed."""
    if not getattr(transport, "checks_robots", False):
        host=urlsplit(url).hostname
        if host not in transport.robots:
            try:
                text=transport.get('https://'+host+'/robots.txt',max_bytes=100000,kind='robots')
                rules=RobotFileParser();rules.parse(text.lstrip('\ufeff').splitlines());transport.robots[host]=rules
            except FetchError as e:transport.robots[host]=True if str(e)=='http_404' else False
        rules=transport.robots[host]
        if rules is False or (rules is not True and not rules.can_fetch(USER_AGENT,url)):raise FetchError('robots_disallowed_or_unavailable')
        if rules is not True and (rules.crawl_delay(USER_AGENT) or 0)>1:raise FetchError('stricter_crawl_delay_skip')
    text=transport.get(url,**kwargs)
    if re.search(r'<title[^>]*>\s*(?:Just a moment|Please.*(?:robot|verify))',text,re.I):raise FetchError('access_challenge_not_bypassed')
    return text


def safe_xml(text):
    if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():raise ValueError('unsupported XML declaration')
    return ET.fromstring(text)


def impact_records(text):
    """RSS explicitly carries employer, location and full published job text."""
    root=safe_xml(text);rows=[];seen=set()
    for item in root.findall('./channel/item')[:1000]:
        url=item.findtext('link') or '';u=urlsplit(url)
        if not safe_public_url(url) or u.hostname!='impactopportunity.org' or not re.fullmatch(r'/job/\d+/[^/]+/?',u.path):continue
        title=item.findtext('title') or '';company=item.findtext('{http://purl.org/dc/elements/1.1/}creator') or ''
        raw=item.findtext('description') or '';header=raw.split('<',1)[0]
        lines=[x.strip() for x in header.splitlines() if x.strip()]
        location=lines[0] if lines else ''
        body=strip_html(raw);source_id=u.path.split('/')[2];identity='impactopportunity:'+source_id
        if not title or identity in seen:continue
        seen.add(identity)
        rows.append(dict(source='impactopportunity',source_id=source_id,identity=identity,provider='public_rss',
            board_key='public_rss:impactopportunity',title=title,company=company,
            company_evidence='RSS_creator_advertiser_may_be_recruiter_not_ultimate_employer',location=location,
            raw_location=location,raw_locations=[location],geography=geography(location),url=url,index_url=IMPACT_FEED,
            posted_at=item.findtext('pubDate') or '',updated_at='',closing_at='',retrieved_at=stamp(),
            description=body[:30000],description_sha256=hashlib.sha256(body.encode()).hexdigest(),description_truncated=len(body)>30000,
            detail_status='published_RSS_description_observed',record_kind='advertised_position',
            description_scope='RSS_body_requirements_completeness_not_established',
            evidence_status='public_board_feed_not_employer_confirmed_active',employer_board_urls=observed_ats_urls(raw)))
    return rows


def retrieve_impact(transport):
    before=len(transport.requests);stats=dict(status='ok',errors=[],received=0,unique=0,capped=False,requests=0)
    rows=[]
    try:
        text=public_text(transport,IMPACT_FEED,max_bytes=4_000_000,kind='public_feed')
        rows=impact_records(text);stats.update(received=len(rows),unique=len(rows),capped=True)
        if not rows:raise FetchError('feed_empty_or_schema_changed')
    except (FetchError,ValueError,ET.ParseError) as e:stats.update(status='failed',errors=[str(e)[:100]])
    stats['requests']=len(transport.requests)-before
    return rows,stats


def discover_jazz_sitemap(transport,registry,page_budget=1,market=None):
    """Read bounded prefixes of sitemaps advertised in JazzHR robots.txt.

    URL slugs are *hints*, not live listings, location/experience confirmation or
    reliable freshness. Truncated XML contributes only complete <url> elements.
    """
    logs=[]
    if not 0<=page_budget<=5:raise ValueError('Sitemap budget must be 0–5')
    offset=registry.get('jazz_sitemap_offset',0)
    for i in range(page_budget):
        page=(offset+i)%5;url=JAZZ_SITEMAP+str(page)
        log={'url':url,'status':'ok','complete_URL_entries':0,'new_boards':0,'prefix_limited':True}
        before=len(registry['boards'])
        try:
            text=public_text(transport,url,max_bytes=1_500_000,kind='seed_sitemap',prefix=True)
            if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():raise ValueError('unsupported XML declaration')
            for block in re.findall(r'<url(?:\s[^>]*)?>.*?</url>',text,re.S)[:10000]:
                node=safe_xml(block);loc=node.findtext('loc') or '';board=board_from_url(loc)
                if not board or board['provider']!='jazzhr':continue
                log['complete_URL_entries']+=1
                slug=urlsplit(loc).path.rsplit('/',1)[-1]
                register(registry,loc,dict(kind='public_provider_sitemap',url=url,observed_url=loc,
                    job_title_hint=slug.replace('-',' '),geography_hint=url_location_hint(slug,market),market_id=market.identifier if market else 'dc-dmv',
                    note='Lossy URL hint only; timestamp is not a reliable original posting date.'))
            log['new_boards']=len(registry['boards'])-before
            if not log['complete_URL_entries']:log['status']='empty_or_schema_changed'
        except (FetchError,ValueError,ET.ParseError) as e:log['status']=str(e)[:100]
        logs.append(log)
        if any(t in log['status'] for t in ('403','429','budget','host_stopped')):break
    registry['jazz_sitemap_offset']=(offset+len(logs))%5
    return logs
