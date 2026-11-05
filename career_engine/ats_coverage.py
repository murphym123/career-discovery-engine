"""Source coverage measurements, never candidate-fit ranking or job exclusion."""
import hashlib
import re
from collections import Counter,defaultdict
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode

from career_engine.ats import posting_key
from career_engine.discovery import geography,expired
from career_engine.review_quarantine import ReviewQuarantine

FAMILIES={
 'policy_government_affairs':r'\b(policy|legislat\w*|government (?:affairs|relations)|public affairs|lobby\w*|regulat\w*)\b',
 'research_consulting':r'\b(research|consult\w*|advis\w*|analys\w*|intelligence|strategy)\b',
 'programs_operations':r'\b(program\w*|operations?|coordinat\w*|administrator|administrative|office|events?|meeting\w*)\b',
 'communications':r'\b(communications?|editor\w*|press|writing|reporter|media|content|public relations)\b',
 'development_associations':r'\b(development|fundrais\w*|donor|membership|partnership\w*|grants?)\b',
 'executive_support':r'\b(executive assistant|special assistant|staff assistant|executive support|chief of staff)\b',
 'advocacy_organizing':r'\b(advoca\w*|organiz(?:er|ing)|campaign\w*|coalition|engagement|outreach)\b',
 'specialist_or_clinical_signal':r'\b(engineer\w*|developer|physician|nurse|therapist|scientist|attorney|counsel|accountant)\b',
}


def canonical(url):
    try:
        u=urlsplit(url)
        q=urlencode(sorted((k,v) for k,v in parse_qsl(u.query) if not k.startswith('utm_') and k not in ('gh_src','lever-source','source','ref')))
        return urlunsplit(('https',str(u.hostname).lower(),u.path.rstrip('/'),q,''))
    except (ValueError,TypeError):return ''


def name_keys(company):
    company=str(company or '')
    def norm(text):return ' '.join(w for w in re.sub(r'[^a-z0-9]+',' ',text.lower()).split() if w not in ('the','of','and','inc','llc','ltd'))
    values={norm(re.sub(r'\([^)]*\)','',company))}
    values.update(norm(x) for x in re.findall(r'\(([^)]+)\)',company))
    values.update(norm(x) for x in re.split(r'\s*/\s*',re.sub(r'[()]','/',company)) if len(norm(x).split())>=2)
    return values-{'','confidential','unknown'}


def title_key(title):
    text=str(title).lower()
    text=re.sub(r'\s*[-–—]\s*washington,?\s*d[. ]?c[.]?\s*$','',text)
    text=re.sub(r'^(?:dc|washington,? d[. ]?c[.]?)\s+','',text)
    return re.sub(r'[^a-z0-9]+',' ',text).strip()


def early_signal(row):
    body=str(row.get('description') or '')
    # Explicit short ranges only; this is quoted evidence, not a requirement or
    # qualification determination. Other longer minimums can contradict it.
    hits=[]
    for m in re.finditer(r'(?i)\b(0|1|2|3|one|two|three)\s*(?:[-–—]|to)\s*(\d|one|two|three|four)\s*(?:\+\s*)?years?\b|\b(?:no (?:prior |previous )?experience (?:is )?(?:required|necessary)|entry.level)\b',body):
        fragment=body[max(0,m.start()-80):m.end()+150]
        if re.search(r'experience|entry.level',fragment,re.I):hits.append(fragment)
    higher=bool(re.search(r'(?i)(?:minimum|at least)\s+(?:of\s+)?(?:[5-9]|1\d|five|six|seven|eight|nine|ten)\s*(?:\(\d+\))?\s*\+?\s*years?\b',body))
    if hits:return {'kind':'mixed_ranges_review' if higher else 'description_early_career_signal','evidence':hits[:2],
                    'note':'Not proof of eligibility: check mandatory vs preferred scope, degree, pay/student status and other requirements.'}
    if re.search(r'\b(assistant|associate|coordinator|junior|entry.level|intern)\b',row.get('title',''),re.I):
        return {'kind':'title_only_review','evidence':[],'note':'Title alone does not establish level or qualifications.'}
    return {'kind':'no_signal_or_unknown','evidence':[],'note':'No negative fit inference; missing or unusual evidence remains reviewable.'}


def assess_increment(base,rows):
    quarantine=ReviewQuarantine()
    old_urls={canonical(r.get('url','')):r for r in base if r.get('url')}
    old_postings={}
    old_links=defaultdict(list)
    old_by_url={r.get('url',''):r for r in base}
    old_pairs=defaultdict(list)
    for r in base:
        for url in [r.get('url',''),*(r.get('employer_board_urls',[]) if isinstance(r.get('employer_board_urls'),list) else [])]:
            key=posting_key(url)
            if key:
                if url==r.get('url'):old_postings[key]=r
                else:old_links[key].append(r)
        for name in name_keys(r.get('company')):old_pairs[(name,title_key(r.get('title','')))].append(r)
    groups=defaultdict(list);seen={};counts=Counter()
    for r in rows:
        same=[];method=''
        key=posting_key(r['url']) or r['identity']
        if canonical(r['url']) in old_urls:same=[old_urls[canonical(r['url'])]];method='same_canonical_url'
        elif key in old_postings:same=[old_postings[key]];method='same_ATS_posting_id'
        likely=[]
        if not same:
            for name in name_keys(r['company']):likely+=old_pairs.get((name,title_key(r['title'])),[])
            # Outbound links can be generic/incorrect/related jobs. They are
            # corroborating review evidence, never proof of a duplicate vacancy.
            likely+=old_links.get(key,[])
            for evidence in r.get('board_discovery_evidence',[]):
                previous=old_by_url.get(evidence.get('url','')) if isinstance(evidence,dict) else None
                if previous and title_key(previous.get('title',''))==title_key(r['title']):likely.append(previous)
        within=seen.get(key)
        r['duplicate_status']='exact_existing_source' if same else 'repeat_ATS_identity' if within else 'possible_crosspost_review' if likely else 'not_matched_to_five_source_capture'
        r['duplicate_evidence']={'method':method if same else ('source_link_or_employer_title_requires_review' if likely else ''),
                                'source_urls':sorted({x.get('url','') for x in same+likely}),
                                'related_ATS_identity':within or ''}
        seen.setdefault(key,r['identity'])
        r['independent_review_quarantined']=quarantine.matches(r)
        r['function_signals']=[] if r['independent_review_quarantined'] else [f for f,p in FAMILIES.items() if re.search(p,r['title'],re.I)]
        r['early_career_signal']=({'kind':'withheld_for_independent_review','evidence':[],'note':'Retained unranked; no automatic evaluation.'} if r['independent_review_quarantined'] else early_signal(r))
        r['geography']=geography(r['location']);r['expired_by_supplied_date']=expired(r)
        r['potential_talent_pool_title']=bool(re.search(r'(?i)^(?:general (?:interest|application)|talent (?:pool|community)|pitch your position!?|future opportunities|expression of interest|open application)(?:\s*[-–|:].*)?$',r['title'].strip()))
        r['coverage_status']='regional_geography_review' if r['geography'] in ('unknown','regional_worksite_unknown','remote_eligibility_unknown') else 'outside_DC_region' if r['geography']=='elsewhere' else 'DC_DMV_label' if r['geography']=='dc_dmv' else 'US_remote_label_not_DC_worksite'
        groups[(tuple(sorted(name_keys(r['company']))),title_key(r['title']))].append(r)
        counts[r['duplicate_status']]+=1
    for key,group in groups.items():
        if len(group)>1:
            gid=hashlib.sha256(repr(key).encode()).hexdigest()[:16]
            for r in group:r['possible_same_employer_title_group']={'id':gid,'count':len(group),'action':'flag_only_distinct_requisitions_retained'}
    local=[r for r in rows if r['geography']=='dc_dmv']
    local_new=[r for r in local if r['duplicate_status']=='not_matched_to_five_source_capture' and not r['expired_by_supplied_date'] and r['record_kind']!='prospect_pool' and not r['potential_talent_pool_title']]
    return {'raw_ATS_source_identities':len(rows),'distinct_board_keys':len({r['board_key'] for r in rows}),
            'dc_dmv_labels':len(local),'us_remote_labels':sum(r['geography']=='us_remote' for r in rows),
            'geography_review':sum(r['geography'] in ('unknown','regional_worksite_unknown','remote_eligibility_unknown') for r in rows),
            'talent_or_prospect_pool_signals':sum(r['record_kind']=='prospect_pool' or r['potential_talent_pool_title'] for r in rows),
            'outside_region':sum(r['geography']=='elsewhere' for r in rows),'duplicates':dict(counts),
            'dc_not_matched_to_five_sources':len(local_new),
            'dc_new_conservative_employer_title_groups':len({(tuple(sorted(name_keys(r['company']))) or (r['board_key'],),title_key(r['title'])) for r in local_new}),
            'dc_new_title_specialist_signals':sum('specialist_or_clinical_signal' in r['function_signals'] for r in local_new),
            'independent_review_quarantined':sum(r['independent_review_quarantined'] for r in rows),
            'dc_possible_crossposts':sum(r['duplicate_status']=='possible_crosspost_review' for r in local),
            'dc_new_with_description':sum(bool(r['description']) for r in local_new),
            'dc_new_description_early_signals':sum(r['early_career_signal']['kind']=='description_early_career_signal' for r in local_new),
            'dc_new_title_only_signals':sum(r['early_career_signal']['kind']=='title_only_review' for r in local_new),
            'dc_new_function_signals':dict(Counter(f for r in local_new for f in r['function_signals'])),
            'note':'Source identities are not necessarily distinct vacancies. No match to a bounded baseline does not prove market-wide novelty or relevance; signals are not qualification findings.'}
