"""Source-independent, offline candidate triage. Scores are review priorities, not odds.

Never accepts analyst evaluation labels as input. No network/model/API dependency.
Requirements are heuristic extractions with provenance, not verified qualifications.
"""
import hashlib
import re
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from urllib.parse import urlsplit

from career_engine.discovery import geography, expired

VERSION = 'evidence-triage-v1.3-diagnostic'
# Public, non-identifying capability assertions from issue #7 / assignment. These
# are reported experience, not documentary verification, durations or outcomes.
PROFILE = {
    'degree': 'BA political science', 'target_years': 3,
    'evidence_basis': 'User-reported capabilities in issue #7 and Phase 3 assignment; not independently verified.',
    'capabilities': {
        'research': 'Political/opposition research, public records and academic research.',
        'writing': 'Political/policy writing, editing and analytical memoranda.',
        'programs': 'Educational programs, fellowships and organizational support.',
        'events': 'Event coordination and logistics.',
        'outreach': 'Campus recruitment, outreach and stakeholder communication.',
        'crm': 'Salesforce/CRM administration.',
        'operations': 'Organizational administration and operational support.',
        'organizing': 'Campaign strategy, advocacy and organizing experience; scope/duration unverified.',
        'teaching': 'Teaching and intellectual-program experience.',
        'ai_policy': 'Some AI-policy research/writing; technical AI expertise not established.',
    },
    'unknowns': ['Exact non-overlapping professional years and depth by function',
                 'Licensure, clearances, specialized credentials, work authorization, driving and travel availability',
                 'Professional portfolios, quantitative/technical proficiency and outcomes',
                 'Strength relative to actual applicants'],
}
CAPABILITIES = {
    'research': r'\b(research|investigat\w*|public.records|analys(?:is|t|es)|analytical)\b',
    'writing': r'\b(writ\w*|edit\w*|proofread\w*|memos?|memoranda|correspondence|reporting|journalis\w*)\b',
    'programs': r'\b(program\w*|fellowship\w*|participant\w*|educational)\b',
    'events': r'\b(events?|logistics|meetings?|conferences?|travel arrangements)\b',
    'outreach': r'\b(outreach|recruit\w*|constituent\w*|stakeholder\w*|engagement|communication\w*)\b',
    'crm': r'\b(salesforce|crm|database\w*|donor records)\b',
    'operations': r'\b(administrati\w*|operations?|office|calendar\w*|records management|document management|executive support)\b',
    'organizing': r'\b(organiz(?:ing|er\w*)|campaign\w*|advocacy|political strategy)\b',
    'teaching': r'\b(teaching|curricul\w*|instructors?|learning|training)\b',
    'ai_policy': r'\b(ai|artificial intelligence).{0,35}\b(policy|research|safety|security)\b',
}
FAMILIES = {
    'policy_legislative': r'\b(policy|legislat\w*|government affairs|lobby\w*|regulatory|politic\w*)\b',
    'research_consulting': r'\b(research|analys\w*|investigat\w*|consult\w*|intelligence)\b',
    'programs_operations': r'\b(program\w*|coordinat\w*|operations?|administrati\w*|events?|secretary|executive assistant|staff assistant)\b',
    'communications': r'\b(communication\w*|reporter|journalis\w*|editor\w*|media|public affairs|writing)\b',
    'advocacy_campaigns': r'\b(advocacy|organiz(?:ing|er)|campaign\w*|labor representative|grievance)\b',
    'development_associations': r'\b(development|fundrais\w*|donor\w*|membership|association\w*|partnership\w*)\b',
    'education_adjacent': r'\b(education\w*|teaching|curricul\w*|fellowship\w*|publishing|library)\b',
}
PREFERENCE = re.compile(r'\b(prefer\w*|desir\w*|welcome|advantage\w*|helpful|a plus|bonus|nice to have|not required|not essential|not necessary|would be an asset)\b',re.I)
MANDATORY = re.compile(r'\b(required|requires?|must|minimum|at least|essential|qualified candidates will|qualified applicants|qualifications|expected|need to have)\b',re.I)
EQUIVALENT = re.compile(r'\b(equivalent|equivalency|in lieu|combination of education|education.{0,12}experience combination|substitut\w*)\b',re.I)
TRAINABLE = re.compile(r'\b(can be learned|ability to learn|will (?:learn|train)|on.the.job|cross.training|bootcamp|training provided)\b',re.I)
NUMBERS = {'zero':0,'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,'nine':9,'ten':10,'eleven':11,'twelve':12,'fifteen':15}
NUM = r'(?:\d{1,2}|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen)'
YEARS = re.compile(rf'\b(?P<lo>{NUM})(?:\s*\(\d{{1,2}}\))?\s*(?:(?:-|–|—|to)\s*(?P<hi>{NUM}))?\s*(?:\+|or more)?\s*(?:(?!(?:{NUM})\b)[a-z][a-z/-]*\s+){{0,5}}years?\b',re.I)
CREDENTIALS = {
    'law_degree': r'\b(j\.?\s?d\.?|juris doctor(?:ate)?|law degree|lawyers?|attorneys?)\b',
    'bar_admission': r'\b(bar admission|admission to.{0,15}bar|admitted to practice|licensed to practice law|active.{0,12}bar|bar membership)\b',
    'advanced_degree': r'\b(ph\.?d\.?|doctor(?:al|ate)|master[’\']?s|mlis)\b',
    'accounting_credential': r'\b(cpa|degree in accounting|accounting (?:bachelor|associate)|accounting degree)\b',
    'legal_practice': r'\bexperience representing.{0,100}criminal proceedings\b',
    'clinical_license': r'\b(registered nurse|nursing licen\w*|medical licen\w*|physician licen\w*|md degree|licensed clinical)\b',
    'active_clearance': r'\b(current.{0,30}(?:secret|clearance)|active.{0,20}clearance|top secret|sci access)\b',
    'technical_degree': r'\b(?:degree in (?:computer science|engineering|environmental)|environmental degree)\b',
}
SPECIALIST = re.compile(r'\b(stenograph\w*|counterintelligence|analytical tradecraft|threat.assessment expertise|specialized government experience|deep.{0,35}expertise|high.level.{0,35}expertise|hands.on US people.operations|HR.compliance experience|major.gifts record|professional.{0,25}reporting (?:track record|portfolio)|specialized foreign.assistance knowledge|energy.policy expertise|specialist debt expertise|fisheries policy|experience with the labor movement|one to two years labor.movement experience|demonstrated experience working with elected leaders|executive.office (?:support|capacity)|track record of strong reporting|prior congressional experience|specific legal.{0,20}database|specialized legal.{0,20}database)\b',re.I)
LEADERSHIP = re.compile(r'\b(executive.level|senior financial.management|senior leadership|lead.{0,25}(?:staff teams|expert teams|fundraising strategy)|direct comprehensive|leadership experience|strategic leadership|program/personnel management|personnel management|supervis(?:e|ory).{0,30}(?:staff|team|coordinators))\b',re.I)
BOILERPLATE = re.compile(r'equal opportunity|do not discriminate|does not discriminate|protected veteran|reasonable accommodation|regardless of|\bapplicants with disabilities\b|all lawyer positions require',re.I)


def _hits(patterns, text):
    return [name for name, pattern in patterns.items() if re.search(pattern,text,re.I)]


def identity(job):
    if job.get('identity'): return str(job['identity'])
    if job.get('source_id'): return f"{job.get('source','unspecified')}:{job['source_id']}"
    return hashlib.sha256(str(job.get('url') or (job.get('company','')+'|'+job.get('title',''))).encode()).hexdigest()[:24]


def clauses(text):
    # Preserve common credential abbreviations; keep semicolon qualification
    # contexts local so a preferred JD does not taint an earlier mandatory BA.
    text = re.sub(r'J\.D\.', 'JD', text, flags=re.I)
    text = re.sub(r'Ph\.D\.?', 'PhD', text, flags=re.I)
    text = re.sub(r'(?<!^)(?=\b(?:(?<!Preferred )(?<!Desired )(?<!Basic )(?<!Minimum )(?<!Required )Qualifications|Education:|Experience:|Education and Experience|Education & Experience|Education and/or Experience|Minimum Years|Necessary Knowledge|Desired Qualifications|Preferred Qualifications|Basic Qualifications|Minimum Qualifications|Required Qualifications|Required Tests|What You.ll Bring|What You Bring|Skills and Competencies|Salary & benefits|Application instructions)\b)', '\n', text)
    parts = re.split(r'(?<=[.!?;])\s+|[\n\r•]+|(?=\b(?:Preferred Qualifications|Basic Qualifications|Minimum Qualifications|Required Qualifications|Desired Qualifications|Qualifications:|Required:|Preferred:|Duties:))',text)
    context = 'unspecified'
    for part in parts:
        part = part.strip()
        if not part: continue
        if re.match(r'(?:Preferred|Desired|Nice to have)',part,re.I): context='preferred'
        elif re.match(r'(?:Basic|Minimum|Required|Qualifications|Education and|Education &|Experience and skills|Necessary Knowledge)',part,re.I): context='required'
        elif re.match(r'(?:Duties|Responsibilities|Salary|Benefits|Application|How to apply|About us)',part,re.I): context='unspecified'
        if BOILERPLATE.search(part): continue
        yield part, context


def _span_modality(clause,start,end,context):
    # Cues immediately following an item ('degree preferred') beat a distant
    # section header. A preference about another requirement must not propagate.
    before=clause[max(0,start-100):start]
    after=clause[end:end+260]
    immediate_after=re.match(r"\s*(?:is |are |strongly |highly |certainly )*(preferred|desired|a plus|helpful|not required|not essential|required|essential)\b",after,re.I)
    if not immediate_after:
        later=re.search(r'\b(preferred|desired|a plus|not required|not essential|required|essential)\b',after,re.I)
        if later and not re.search(r'\b(?:must|minimum|bachelor|master|JD|PhD)\b',after[:later.start()],re.I):
            return 'required' if later[1].lower() in ('required','essential') else 'preferred'
    if immediate_after:
        return 'required' if immediate_after[1].lower() in ('required','essential') else 'preferred'
    cues=[]
    for pattern,mod in [(PREFERENCE,'preferred'),(MANDATORY,'required')]:
        cues.extend((m.end(),mod) for m in pattern.finditer(before) if m.group().lower()!='qualifications')
    if cues:return max(cues)[1]
    return context


def requirements(text):
    result=[]
    for clause,context in clauses(text):
        credential_matches=[(kind,m) for kind,pattern in CREDENTIALS.items() for m in re.finditer(pattern,clause,re.I)]
        matches=list(YEARS.finditer(clause))
        if not (credential_matches or matches or MANDATORY.search(clause) or PREFERENCE.search(clause) or EQUIVALENT.search(clause) or TRAINABLE.search(clause)):
            continue
        def base_at(start,end):
            return {'text':clause[max(0,start-130):min(len(clause),end+260)],
                    'modality':_span_modality(clause,start,end,context),
                    'equivalence':bool(EQUIVALENT.search(clause[max(0,start-160):end+220])),
                    'trainable':bool(TRAINABLE.search(clause[max(0,start-80):end+80]))}
        for match in matches:
            around=clause[max(0,match.start()-65):match.end()+100]
            if not (re.search(r'experience|professional|organizing|administrative|fundraising|related work|post.law|curriculum|relevant|expected|supervisory|leadership|compliance',around,re.I) or context=='required'): continue
            lo=match['lo'].lower(); hi=(match['hi'] or match['lo']).lower()
            result.append({**base_at(match.start(),match.end()),'kind':'experience',
                           'minimum_years':int(lo) if lo.isdigit() else NUMBERS[lo],
                           'maximum_years':int(hi) if hi.isdigit() else NUMBERS[hi]})
        for kind,m in credential_matches:
            if kind=='law_degree' and context=='unspecified' and not re.search(r'candidate|applicant|must|required|degree|qualification|education|lawyer with',clause,re.I): continue
            local=clause[max(0,m.start()-35):m.end()+45]
            if re.search(r'not an? (?:attorney|lawyer|nursing licen\w*)|no (?:law degree|nursing licen\w*) (?:requirement|required)',local,re.I):continue
            result.append({**base_at(m.start(),m.end()),'kind':kind})
        if not credential_matches and not matches:
            modality='preferred' if PREFERENCE.search(clause) else 'required' if MANDATORY.search(clause) else context
            result.append({'text':clause[:650],'modality':modality,'equivalence':bool(EQUIVALENT.search(clause)),
                           'trainable':bool(TRAINABLE.search(clause)),'kind':'skill_or_condition'})
    return result


def _valid_url(value):
    try:
        u=urlsplit(value)
        return u.scheme in ('http','https') and bool(u.hostname)
    except ValueError:
        return False


def preliminary(job):
    text=' '.join(str(job.get(k,'') or '') for k in ('title','snippet','category'))[:5000]
    families=_hits(FAMILIES,text)
    return {'families':families,'relevance_priority':min(40,10*len(families))+(10 if geography(job.get('location','')) in ('dc_dmv','us_remote') else 0),
            'geography':geography(job.get('location','')),
            'description_available':bool(str(job.get('description','') or '').strip()),
            'title_flags':_hits({'senior_title':r'\b(senior|director|chief|lead|manager|counsel)\b',
                                 'specialist_title':r'\b(engineer|nurse|physician|accountant|attorney)\b'},job.get('title',''))}


def plan_detailed(jobs,budget=200,seed=0):
    """Budget bounds detailed CPU work, never retention. No per-record API calls.

    Four fifths relevance + employer/source/function round robin; one fifth
    stable score-independent exploration. Missing descriptions stay investigated.
    """
    if budget < 0: raise ValueError('detailed budget must be nonnegative')
    described=[j for j in jobs if j['_preliminary']['description_available']]
    if len(described)<=budget: return {j['identity']:'all_available_descriptions' for j in described}
    groups=defaultdict(list)
    for j in described:
        p=j['_preliminary']; groups[(j.get('source',''),p['families'][0] if p['families'] else 'unfamiliar',j.get('company',''))].append(j)
    for key,values in list(groups.items()): groups[key]=deque(sorted(values,key=lambda j:(-j['_preliminary']['relevance_priority'],j['identity'])))
    # Interleave employers within relevance bands; stable order independent of
    # source iteration order. Every group eventually appears as budget grows.
    ranked=[]
    while groups:
        for key in sorted(groups,key=lambda k:(-groups[k][0]['_preliminary']['relevance_priority'],k)):
            ranked.append(groups[key].popleft())
            if not groups[key]: del groups[key]
    explore= max(1,budget//5) if budget else 0
    chosen={j['identity']:'relevance_and_variety' for j in ranked[:budget-explore]}
    tail=[j for j in described if j['identity'] not in chosen]
    tail.sort(key=lambda j:hashlib.sha256(f"{seed}|{j['identity']}".encode()).hexdigest())
    chosen.update({j['identity']:'score_independent_exploration' for j in tail[:budget-len(chosen)]})
    return chosen


def _condition_gaps(reqs):
    risks=[]
    for r in reqs:
        text=r['text']
        if r['modality']!='preferred' and re.search(r'work authoriz|citizen|driver.?s? licen|insured car|travel|eligib|GS.\d|security clearance',text,re.I):
            risks.append({'kind':'eligibility_or_conditions','evidence':text,'assessment':'Not established by capability profile; verify before applying.'})
    return risks


def evaluate(job,profile=None,as_of=None,detailed=True):
    profile=profile or PROFILE
    p=job.get('_preliminary') or preliminary(job)
    body=str(job.get('description','') or '')
    limited=len(body)>60000
    body=body[:60000] if detailed else ''
    title=str(job.get('title',''))
    alltext=title+' '+body
    families=_hits(FAMILIES,alltext)
    rel=min(100,len(families)*12+ (25 if families else 0))
    # Candidate capability support must occur in the description, never merely
    # in an employer/title, and explicitly cannot prove domain expertise.
    evidence=[]
    for name in _hits(CAPABILITIES,body):
        if name not in profile['capabilities']: continue
        fragment=next((c for c,_ in clauses(body) if re.search(CAPABILITIES[name],c,re.I)), '')
        if fragment:
            evidence.append({'capability':name,'candidate_evidence':profile['capabilities'][name],
                             'candidate_evidence_basis':profile['evidence_basis'],
                             'posting_evidence':fragment[:450],
                             'support':'Transferable function only; does not establish duration, domain mastery or every requirement.'})
    reqs=requirements(body) if detailed else []
    hard=[]; risks=[]; preferences=[]; compatible=[]
    for r in reqs:
        kind=r['kind']; mod=r['modality']; text=r['text']
        if mod=='preferred' or r['trainable']:
            preferences.append(r)
            if kind=='experience' and r['minimum_years']>profile['target_years']:
                risks.append({'kind':'preferred_experience_gap','evidence':text,'assessment':'Preference only, not mandatory; materially higher experience may reduce competitiveness.'})
            continue
        # Unspecified credentials are review questions, not mandatory blockers.
        if kind in CREDENTIALS:
            item={'kind':kind,'evidence':text,'assessment':'No supporting credential in supplied profile; verify requirement and candidate evidence.'}
            if kind=='active_clearance' and re.search(r'ability to obtain|eligible.{0,15}obtain|able to obtain',text,re.I):
                item['assessment']='Clearance may be obtained after selection; eligibility/timeline unknown, not an established current-credential blocker.'
                risks.append(item)
            elif mod=='required' and not r['equivalence']: hard.append(item)
            else: risks.append(item)
        elif kind=='experience':
            yrs=r['minimum_years']
            item={'kind':'experience','evidence':text,'minimum_years':yrs,
                  'assessment':'Exact duration, specialized scope and allowable equivalence must be verified.'}
            if yrs>=5 and mod=='required': hard.append(item)
            elif yrs>profile['target_years'] or r['maximum_years']>profile['target_years'] or mod=='unspecified': risks.append(item)
            else: compatible.append(item)
    risks+=_condition_gaps(reqs)
    specialized=[c[max(0,m.start()-80):m.end()+140] for c,ctx in clauses(body) for m in SPECIALIST.finditer(c) if _span_modality(c,m.start(),m.end(),ctx)!='preferred']
    leadership=[c[max(0,m.start()-80):m.end()+140] for c,ctx in clauses(body) for m in LEADERSHIP.finditer(c) if _span_modality(c,m.start(),m.end(),ctx)!='preferred']
    if specialized: risks.append({'kind':'specialized_scope','evidence':'; '.join(specialized)[:900],'assessment':'Profile does not establish this specialized professional skill; interest and general research are insufficient.'})
    if leadership: risks.append({'kind':'leadership_scope','evidence':'; '.join(leadership)[:900],'assessment':'Required leadership depth is not established by support experience.'})
    if limited or str(job.get('description_truncated','')).lower() in ('true','1'):
        risks.append({'kind':'truncated_description','evidence':'Description was truncated.','assessment':'Requirements beyond retained text unknown.'})
    if any(r['equivalence'] for r in reqs):
        risks.append({'kind':'equivalence_review','evidence':'Posting permits an equivalence or alternative route.','assessment':'Do not add overlapping internships/education automatically; establish which route the employer accepts.'})
    complete=str(job.get('qualifications_complete','')).lower() not in ('false','0')
    # Descriptive fragments without any requirement evidence remain uncertain.
    qualifications_seen=bool(reqs) and complete
    uncertainty=['Capabilities are user-reported; precise years, work samples and applicant-pool strength unverified.',
                 'Board observation is not an independent employer-active verification.']
    if not body: uncertainty.append('Description awaits detailed budget; no negative qualification inference made.' if p['description_available'] else 'Missing description: no negative qualification inference made.')
    elif not qualifications_seen: uncertainty.append('Complete qualification requirements not established by supplied text.')
    if p['geography'] not in ('dc_dmv','us_remote'): uncertainty.append('Worksite/remote eligibility needs verification.')
    if p['geography']=='us_remote': uncertainty.append('US-remote board label may hide state, timezone, work-authorization or travel restrictions.')
    if job.get('description_representation')=='analyst_nonverbatim_abstract': uncertainty.append('Analysis uses a factual abstract, not full original posting; omissions may change conclusions.')
    closed=expired(job,as_of)
    unresolved=str(job.get('opportunity_eligibility_unresolved','')).lower() in ('true','1') or re.search(r'\b(multiple.{0,25}(?:positions|levels)|internships?/fellowships?|events? intern|student.oriented|current(?:ly enrolled)? students?|undergraduate students?|graduate students?|compensation.{0,30}(?:unclear|verify))\b',alltext,re.I)
    major_risks=[r for r in risks if r['kind'] in ('experience','preferred_experience_gap','specialized_scope','leadership_scope',*CREDENTIALS)]
    if not _valid_url(str(job.get('url',''))) or not title.strip(): decision='invalid_record'
    elif closed: decision='closed_by_source_date'
    elif p['geography']=='elsewhere': decision='location_review'
    elif not body or not detailed: decision='investigate'
    elif job.get('source')=='brave' and job.get('extraction_status')!='jobposting_metadata': decision='investigate'
    elif hard: decision='qualification_gap'
    elif unresolved or not qualifications_seen: decision='investigate'
    elif rel<35 or not evidence: decision='adjacent_review'
    elif major_risks: decision='stretch'
    elif len(evidence)>=2 and not (p['title_flags'] and not qualifications_seen): decision='reasonable_application'
    else: decision='investigate'
    if p['geography'] in ('unknown','remote_eligibility_unknown','regional_worksite_unknown') and decision=='reasonable_application': decision='investigate'
    qualification = ('mandatory_support_gap' if hard else 'unknown' if decision=='investigate' else 'scope_or_duration_review' if major_risks else 'potentially_compatible_not_confirmed')
    strength=min(40,len(evidence)*5)
    # No salary, employer size, political ideology, source reputation or title
    # prestige as selection criteria. This number only orders a decision tier.
    priority=rel+strength+(6 if p['geography']=='dc_dmv' else 0)
    advantage=[]
    if len(evidence)>=2:
        advantage.append({'hypothesis':'Combination of '+', '.join(e['capability'] for e in evidence[:4])+' may be useful in this role.',
                          'evidence_capabilities':[e['capability'] for e in evidence[:4]],
                          'confidence':'potential complementarity only; no comparison with actual applicants'})
    verification={'status':'board_observed_not_independently_verified' if job.get('source') in ('senate','unionjobs','globaljobs','onthinktanks','workfornonprofits') else 'source_observed_not_independently_verified',
                  'retrieved_at':job.get('retrieved_at',''),'posted_at':job.get('posted_at',''),
                  'closing_at':job.get('closing_at',''),'expired_by_supplied_date':closed,
                  'employer_active_verified':False}
    return {'identity':identity(job),'title':title,'company':job.get('company',''),'location':job.get('location',''),
            'url':job.get('url',''),'source':job.get('source',''),'sources':job.get('sources') or job.get('source',''),
            'source_evidence':{k:job.get(k,'') for k in ('evidence_status','detail_status','record_kind','raw_location','inspection_stratum')},
            'source_id':job.get('source_id',''),'index_url':job.get('index_url',''),'discovery_urls':job.get('discovery_urls',''),
            'terms':{**{k:job.get(k,'') for k in ('employment_type','work_arrangement','salary','posted_at_raw','closing_at_raw')}, 'salary_text': '; '.join(c[:250] for c,_ in clauses(body) if re.search(r'salary|compensation|\$[0-9]',c,re.I))[:600]},
            'decision':decision,'career_relevance':{'priority':rel,'families':families,'basis':'Observed role functions; broad career adjacency, not qualification.'},
            'qualifications':{'status':qualification,'requirements':reqs,'mandatory_gaps':hard,'compatible_experience_ranges':compatible,
                              'supported_requirements': [{'requirement':'Bachelor-level education in political science or an accepted general field', 'candidate_evidence':profile['degree'], 'support':'Reported degree only; verify any field-specific equivalence.'}] if re.search(r'bachelor.{0,100}(?:political science|any field)',body,re.I) else [],
                              'preferences_or_trainable':preferences,'risks':risks},
            'supported_experience':evidence,'competitive_advantage':advantage,'uncertainty':uncertainty,
            'evidence_confidence':'limited' if not body or not detailed or not qualifications_seen or limited else 'moderate_text_only',
            'review_priority':priority,'geography':p['geography'],'preliminary':p,
            'detailed_analysis':bool(body and detailed),'verification':verification,
            'description_representation':job.get('description_representation','original_supplied_text'),
            'original_description_sha256':job.get('original_description_sha256') or (hashlib.sha256(str(job.get('description','')).encode()).hexdigest() if p['description_available'] else ''),
            'description_chars':len(str(job.get('description','') or ''))}


TIER={'reasonable_application':0,'stretch':1,'investigate':2,'adjacent_review':3,'qualification_gap':4,'location_review':5,'closed_by_source_date':6,'invalid_record':7}


def triage(records,top=15,detailed_budget=200,exploration_slots=10,seed=0,as_of=None,profile=None):
    if top<1 or exploration_slots<0: raise ValueError('invalid shortlist/review budget')
    jobs=[]; seen=Counter()
    for record in records:
        j=dict(record)
        for field in ('title','company','location','url','source','source_id','description','snippet','category'):
            j[field]=str(j.get(field) or '')
        base=identity(j); seen[base]+=1
        # Do not silently overwrite repeated identities from imported snapshots.
        j['identity']=base if seen[base]==1 else f'{base}#sighting{seen[base]}'
        j['_preliminary']=preliminary(j);jobs.append(j)
    plan=plan_detailed(jobs,detailed_budget,seed)
    rows=[]
    for j in jobs:
        row=evaluate(j,profile,as_of,j['identity'] in plan)
        row['inspection_route']=plan.get(j['identity'],'missing_description' if not j['_preliminary']['description_available'] else 'detailed_budget_pending')
        rows.append(row)
    groups=defaultdict(list)
    for row in rows:
        company=re.sub(r'\([^)]*\)','',row['company']).strip().lower()
        title=re.split(r'\s+[–—]\s+',row['title'])[0].strip().lower()
        if company and title: groups[(company,title)].append(row)
    for (company,title),group in groups.items():
        for row in group:
            row['possible_duplicate']={'group':hashlib.sha256((company+'|'+title).encode()).hexdigest()[:16],
                                      'count':len(group),'related_identities':[r['identity'] for r in group[:10] if r['identity']!=row['identity']],
                                      'action':'flag_only_no_merge','reason':'Same normalized employer/title; distinct requisitions may exist.'} if len(group)>1 else None
    ranked=sorted(rows,key=lambda r:(TIER[r['decision']],-r['review_priority'],r['identity']))
    # Soft employer/family variety in shortlist only. Full rankings remain intact.
    candidates=[r for r in ranked if r['decision'] in ('reasonable_application','stretch')]
    shortlist=[];deferred=[];companies=Counter()
    for r in candidates:
        if companies[r['company']]>=3: deferred.append(r)
        else: shortlist.append(r); companies[r['company']]+=1
    shortlist=(shortlist+deferred)[:top]
    chosen={r['identity'] for r in shortlist}
    remaining=[r for r in ranked if r['identity'] not in chosen and r['decision'] not in ('closed_by_source_date','invalid_record')]
    # Separate uncertainty and low-score exploration, including apparent negatives.
    uncertain=sorted([r for r in remaining if r['decision']=='investigate'],key=lambda r:(-r['preliminary']['relevance_priority'],hashlib.sha256(f"{seed}|{r['identity']}".encode()).hexdigest()))
    review=uncertain[:(exploration_slots+1)//2]
    review_ids={r['identity'] for r in review}
    pool=sorted([r for r in remaining if r['identity'] not in review_ids],key=lambda r:hashlib.sha256(f"explore|{seed}|{r['identity']}".encode()).hexdigest())
    review+=pool[:exploration_slots-len(review)]
    return {'version':VERSION,'as_of':(as_of or datetime.now(timezone.utc)).isoformat(),
            'cost':{'network_calls':0,'model_calls':0,'paid_services_enabled':False},
            'profile_basis':(profile or PROFILE)['evidence_basis'],
            'parameters':{'top':top,'detailed_budget':detailed_budget,'exploration_slots':exploration_slots,'seed':seed},
            'counts':{'received':len(jobs),'retained':len(rows),'descriptions_available':sum(j['_preliminary']['description_available'] for j in jobs),
                      'detailed_analyses':len(plan),'shortlisted':len(shortlist),'review_sample':len(review),
                      'decisions':dict(Counter(r['decision'] for r in rows)),
                      'sources':dict(Counter(r['source'] for r in rows)),
                      'shortlist_sources':dict(Counter(r['source'] for r in shortlist)),
                      'shortlist_families':dict(Counter(f for r in shortlist for f in r['career_relevance']['families']))},
            'shortlist':[r['identity'] for r in shortlist],'exploration_queue':[r['identity'] for r in review],
            'investigation_queue':[r['identity'] for r in ranked if r['decision']=='investigate'],
            'audit':ranked}
