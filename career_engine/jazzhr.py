"""Public JazzHR hosted career HTML and JobPosting JSON-LD; no keyed API.

Only published job-description GETs. Never submit forms, fetch candidate state,
use the employer's API key, or call internal JazzHR services.
"""
import hashlib
import json
import re
from difflib import SequenceMatcher
from urllib.parse import unquote, urljoin, urlsplit

from career_engine.ats import FetchError, posting_key, canonical_posting_url, stamp
from career_engine.dc_sources import Document
from career_engine.discovery import geography
from career_engine.extract import safe_public_url, strip_html
from career_engine.inspection import plan_inspections, identity
from career_engine.public_sources import public_text
from career_engine.worksite_evidence import worksite_evidence

EMPTY_BOARD_TEXT = re.compile(
    r"(?:(?:there are |we (?:currently )?have )?(?:currently )?no (?:current |open |available |job )?"
    r"(?:positions|jobs|openings|opportunities)(?: at (?:this|the) time| available)?)[.!]?",
    re.I,
)
CAREER_HEADING = re.compile(r"\b(?:careers?|jobs?|openings|positions|opportunities)\b", re.I)
MORE_JOBS_TEXT = re.compile(r"(?:next(?: page)?|load more(?: jobs)?|show more(?: jobs)?|more jobs|[›»])", re.I)
DETAIL_CAPTURE_PREFIX = "detail:"


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


def _posting_link(board, node):
    if node.tag != 'a' or not node.attrs.get('href'):
        return '', ''
    try:
        url = urljoin(board['url'], node.attrs['href'])
        return url, posting_key(url)
    except ValueError:
        return '', ''


def _owned_posting_link(board, node):
    url, key = _posting_link(board, node)
    if key or not url or not safe_public_url(url):
        return url, key
    path = urlsplit(url)
    bare_id = re.fullmatch(r'/apply/([A-Za-z0-9]{8,100})/?', path.path)
    if bare_id and path.hostname == urlsplit(board['url']).hostname:
        return url, f"{board['key']}:{bare_id[1]}"
    return url, ''


def _has_class(node, value):
    return value in (node.attrs.get('class') or '').split()


def _jazz_index_row(board, name, link, url, key, location):
    return dict(source='ats_jazzhr',source_id=board['token']+':'+key.rsplit(':',1)[1],identity=key,
        provider='jazzhr',board_key=board['key'],title=link.text(),company=name or board['token'],
        company_evidence='public_board_page_title' if name else 'board_identifier_only_not_legal_employer_verified',
        url=canonical_posting_url(url),index_url=board['url'],location=location,raw_location=location,
        raw_locations=[location] if location else [],geography=geography(location),
        description='',description_sha256='',description_truncated=False,posted_at='',updated_at='',closing_at='',
        retrieved_at=stamp(),detail_status='index_only',record_kind='advertised_position',
        evidence_status='employer_public_career_page_not_independently_verified_accepting_applications')


def _same_board_job_link(board, node):
    url, _ = _posting_link(board, node)
    path = urlsplit(url)
    return (path.hostname == urlsplit(board['url']).hostname
            and unquote(path.path).rstrip('/').startswith('/apply/'))


def _job_card(board, card, location):
    # Generic Bootstrap list items also occur in navigation. Require a job
    # heading/location template or a link that attempts this board's job route.
    if _has_class(card.parent, 'list-group') and (location or card.find(lambda n: n.tag == 'h3')):
        return True
    return any(_same_board_job_link(board, node) for node in card.walk())


def _index_indicators(doc, board):
    def visible(node, *, content_only=False):
        while node:
            if (node.tag in ('template', 'noscript') or 'hidden' in node.attrs
                    or (content_only and node.tag in ('form', 'footer', 'nav'))
                    or node.attrs.get('aria-hidden') == 'true'
                    or any(_has_class(node, name) for name in ('hidden', 'd-none'))
                    or re.search(r'(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\b',
                                 node.attrs.get('style') or '', re.I)):
                return False
            node = node.parent
        return True

    continuation = doc.find(lambda n: (
        'next' in (n.attrs.get('rel') or '').lower().split()
        or (n.tag in ('a', 'button') and MORE_JOBS_TEXT.fullmatch(n.text())
            and n.attrs.get('aria-disabled') != 'true' and 'disabled' not in n.attrs
            and not _has_class(n, 'disabled') and not _has_class(n.parent, 'disabled'))
    ))
    heading = doc.find(lambda n: n.tag in ('title', 'h1', 'h2', 'h3')
                       and CAREER_HEADING.search(n.text()) and visible(n, content_only=True))
    empty_notice = heading and doc.find(
        lambda n: n.tag in ('p', 'div', 'li', 'span', 'h2', 'h3') and EMPTY_BOARD_TEXT.fullmatch(n.text())
        and all(visible(child, content_only=True) for child in n.walk())
    )
    # The job parser covers one card template. A visible job link or card in
    # another layout still contradicts an explicit empty notice.
    job_evidence = empty_notice and doc.find(lambda n: visible(n) and (
        _same_board_job_link(board, n)
        or (_has_class(n, 'list-group-item') and not EMPTY_BOARD_TEXT.fullmatch(n.text())
            and _job_card(board, n, n.find(lambda child: _has_class(child, 'list-group-item-text'))))
    ))
    return bool(empty_notice and not job_evidence), bool(continuation)


def jazz_index(html,board,market=None,*,_evidence=None):
    doc=Document(html).root;rows=[];seen=set()
    heading=doc.find(lambda n:n.tag=='title');name=heading.text() if heading else ''
    name=re.sub(r'\s*[-|–]\s*(?:Career Page|Careers|JazzHR).*$', '',name,flags=re.I).strip()
    prefix = board['key'] + ':'
    for card in (n for n in doc.walk() if n.tag=='li' and _has_class(n, 'list-group-item')):
        link=card.find(lambda n: bool(_posting_link(board, n)[1]))
        url, key = _posting_link(board, link) if link else ('', '')
        locations=card.find(lambda n: _has_class(n, 'list-group-item-text'))
        loc=locations.text() if locations else ''
        row = None
        if key.startswith(prefix) and (key not in seen or _evidence is not None):
            row = _jazz_index_row(board, name, link, url, key, loc)
        if _evidence is not None:
            if row is None:
                owned_link = card.find(lambda n: _owned_posting_link(board, n)[1].startswith(prefix))
                if owned_link:
                    observed, owned_key = _owned_posting_link(board, owned_link)
                    _evidence.index_card(_jazz_index_row(board, name, owned_link, observed, owned_key, loc), observed)
                elif _job_card(board, card, locations):
                    _evidence.index_card(None, '')
            else:
                _evidence.index_card(row, url)
        if row is not None and key not in seen:
            seen.add(key)
            rows.append(row)
    for row in rows:row.update(worksite_evidence(row,market))
    if _evidence is not None:
        _evidence.index_finished(name, *_index_indicators(doc, board))
    return rows


def _normalized_title(value):
    return re.sub(r'[^a-z0-9]+', ' ', strip_html(str(value)).lower()).strip()


def _job_posting(doc, title):
    candidates=[]
    def collect(obj):
        if isinstance(obj,list):
            for item in obj:collect(item)
        elif isinstance(obj,dict):
            types=obj.get('@type',[]);types=[types] if isinstance(types,str) else types
            if isinstance(types,list) and 'JobPosting' in types:candidates.append(obj)
            if '@graph' in obj:collect(obj['@graph'])
    for node in doc.walk():
        if node.tag=='script' and node.attrs.get('type')=='application/ld+json':
            try:collect(json.loads(''.join(x for x in node.children if isinstance(x,str))))
            except (ValueError,TypeError):continue
    if not candidates:
        return None
    expected = _normalized_title(title)
    score = lambda candidate: SequenceMatcher(None, _normalized_title(candidate.get('title', '')), expected).ratio()
    chosen = max(candidates, key=score)
    if score(chosen) < .85:
        raise ValueError('public_JobPosting_title_mismatch')
    return chosen


def _jazz_addresses(obj):
    locations = obj.get('jobLocation', [])
    locations = [locations] if isinstance(locations, dict) else locations
    for location in locations if isinstance(locations, list) else ():
        address = location.get('address') if isinstance(location, dict) else None
        if isinstance(address, dict):
            yield {key: address[key] for key in ('addressLocality', 'addressRegion', 'addressCountry')
                   if isinstance(address.get(key), str) and address[key]}


def jazz_geography(html, title, identity_key):
    """Read worksite evidence from an accepted detail receipt during label replay."""
    obj = _job_posting(Document(html).root, title)
    if not obj or (obj.get('url') and posting_key(obj['url']) != identity_key):
        return {}
    sites = [{target: address[key] for key, target in (
        ('addressLocality', 'location'), ('addressRegion', 'state'), ('addressCountry', 'country')
    ) if key in address} for address in _jazz_addresses(obj)]
    context = {'worksites': [site for site in sites if site]}
    if str(obj.get('jobLocationType', '')).upper() == 'TELECOMMUTE':
        context['work_arrangement'] = 'remote'
    return context


def jazz_detail(html,row,market=None):
    doc = Document(html).root
    obj = _job_posting(doc, row['title'])
    if obj is None:
        # Older public JazzHR templates publish Organization JSON-LD and the
        # complete visible description in this dedicated section, without a
        # JobPosting object. Do not read application fields or unrelated text.
        description=doc.find(lambda n:n.attrs.get('id')=='job-description')
        headings=[n.text() for n in doc.walk() if n.tag in ('h1','h2')]
        if not description or not any(SequenceMatcher(
            None, _normalized_title(h), _normalized_title(row['title'])
        ).ratio() >= .85 for h in headings):
            raise ValueError('public_description_section_unavailable_or_title_mismatch')
        body=description.text()
        if not body:raise ValueError('public_description_empty')
        return description_evidence(row,body,'public_JazzHR_HTML_job_description_section',market)
    raw=[', '.join(address.values()) for address in _jazz_addresses(obj)]
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


def retrieve_jazzhr(transport,board,detail_budget=1,market=None,*,_evidence=None):
    start=len(transport.requests);rows=[]
    stats=dict(board_key=board['key'],status='ok',errors=[],warnings=[],received=0,malformed=0,unlisted_omitted=0,capped=False,requests=0)
    def read(url, *, detail_identity=None):
        if _evidence is not None:
            return _evidence.read_page(transport, url, detail_identity=detail_identity)
        return public_text(transport, url, max_bytes=1_000_000, kind='public_html'), None

    try:
        html, body = read(board['url'])
        if _evidence is not None:
            _evidence.capture_index(body, transport.requests[-1]['url'])
        rows=jazz_index(html,board,market,_evidence=_evidence)
        if not rows:raise FetchError('no_public_index_rows_or_schema_changed')
        stats['received']=len(rows);stats['capped']=len(rows)>1000;rows=rows[:1000]
        candidates=sorted([r for r in rows if r['geography']!='elsewhere'],key=lambda r:(r['geography']!='dc_dmv',r['identity']))
        byid={identity(r):r for r in candidates}
        for choice in plan_inspections(candidates,detail_budget,location_classifier=market.label if market else geography):
            row=byid[choice['identity']]
            try:
                html, body = read(row['url'], detail_identity=row['identity'])
                if _evidence is not None and posting_key(transport.requests[-1]['url']) != row['identity']:
                    raise ValueError('posting_ID_mismatch')
                detail = row.copy()
                jazz_detail(html,detail,market)
                if _evidence is not None:
                    _evidence.record_detail(body, detail)
                row.update(detail)
            except (FetchError,ValueError) as e:
                row['detail_status']=str(e)[:100];stats['errors'].append(str(e)[:100])
                if any(x in str(e) for x in ('403','429','budget','host_stopped')):break
        missing_details = [row for row in candidates if not row['description']]
        stats['capped'] = stats['capped'] or bool(missing_details)
        if detail_budget and missing_details:
            for row in missing_details:
                if row['detail_status'] == 'index_only':
                    row['detail_status'] = 'detail_skipped'
            stats['warnings'].append('description_coverage_partial')
    except (FetchError,ValueError) as e:stats['errors'].append(str(e)[:100])
    stats.update(requests=len(transport.requests)-start,unique=len(rows),dc_dmv=sum(r['geography']=='dc_dmv' for r in rows),descriptions=sum(bool(r['description']) for r in rows))
    stats['status']='partial_failure' if rows and (stats['errors'] or stats['warnings']) else 'failed' if stats['errors'] else 'ok'
    stats['inventory']={'scope':'one public hosted-career index response','observed_available_items':stats['received'],
        'metadata_retained':len(rows),'metadata_complete':None,'descriptions_observed':stats['descriptions'],
        'description_denominator_observed_records':len(rows),
        'note':'Visible index rows retained. No independently reported total or proof of undisplayed/paginated inventory completeness.'}
    return rows,stats
