"""Employer/source discovery priority, independent of candidate-fit ranking."""
import hashlib
import math
from collections import Counter

from career_engine.ats_registry import select_boards
from career_engine.discovery import geography
from career_engine.inspection import family


def evidence_strength(board,market=None):
    """Prioritize independently observed worksite/function evidence, not brand."""
    best=0
    for e in board.get('evidence',[]):
        if not isinstance(e,dict):continue
        label=e.get('geography_hint','')
        classifier=market.label if market else geography
        local_label=market.local_label if market else 'dc_dmv'
        local=(label==local_label and (not market or market.identifier=='dc-dmv')) or classifier(e.get('location') or e.get('job_title_hint',''))==local_label
        title=e.get('job_title') or e.get('job_title_hint','')
        observed_function=family({'title':title})!='other'
        if e.get('kind')=='source_audit_link' and local:strength=6 if observed_function else 5
        elif e.get('kind')=='regional_employer_public_link' and local:strength=3  # HQ/link evidence only.
        elif e.get('kind')=='public_provider_sitemap' and local:strength=4 if observed_function else 3
        elif e.get('kind')=='public_provider_sitemap' and market and e.get('market_id')==market.identifier and e.get('geography_hint')=='possible_market':strength=2
        elif e.get('kind')=='public_provider_sitemap' and observed_function:strength=1
        else:strength=0
        best=max(best,strength)
    return best


def select_regional_boards(registry,budget=30,exploration_fraction=.2,market=None):
    if not .2<=exploration_fraction<=1:raise ValueError('At least 20% non-targeted exploration is required')
    legacy=select_boards(registry,budget)  # Same eligibility/run progression, retained comparison strategy.
    run=registry['run']
    eligible=[e for e in registry['boards'].values() if e.get('last_checked_run') is None or
              (isinstance(e.get('last_checked_run'),int) and run-e['last_checked_run']>=(7 if e.get('status')=='failed' else 1))]
    def hashed(e):return hashlib.sha256((str(run)+'|'+e['key']).encode()).hexdigest()
    # No evidence/industry/title/score weighting in this reserved sample.
    exploratory=sorted(eligible,key=lambda e:(e.get('last_checked_run') is not None,hashed(e)))[:math.ceil(budget*exploration_fraction)]
    selected=[];used=set();providers=Counter()
    def add(e,route):
        if e['key'] in used:return
        selected.append(e);used.add(e['key']);providers[e['provider']]+=1
        e['selection_evidence']={'route':route,'strength':evidence_strength(e,market),
            'note':'Board retrieval priority only; no candidate profile, score or suitability inference.'}
    for e in exploratory:add(e,'non_targeted_exploration')
    targeted=[e for e in eligible if evidence_strength(e,market)>1 and e['key'] not in used]
    while targeted and len(selected)<budget:
        e=min(targeted,key=lambda e:(e.get('last_checked_run') is not None,-evidence_strength(e,market),providers[e['provider']],hashed(e)))
        add(e,'regional_or_function_evidence');targeted.remove(e)
    # Weak occupational URL evidence gets a small, separate exploratory budget,
    # never all the remaining slots. Different functions share those slots.
    weak=[e for e in eligible if evidence_strength(e,market)==1 and e['key'] not in used]
    functions=Counter()
    def observed_family(e):
        return next((family({'title':x.get('job_title_hint','')}) for x in e['evidence'] if x.get('job_title_hint')),'other')
    for _ in range(min(budget//4,budget-len(selected))):
        if not weak:break
        e=min(weak,key=lambda e:(e.get('last_checked_run') is not None,functions[observed_family(e)],providers[e['provider']],hashed(e)))
        add(e,'weak_function_exploration');functions[observed_family(e)]+=1;weak.remove(e)
    for e in legacy+sorted(eligible,key=hashed):
        if len(selected)>=budget:break
        add(e,'broad_fill')
    # Interleave the reserved sample with targeted boards so a request cap cannot
    # silently leave all exploration until the very end of the run.
    exploration=[e for e in selected if e['selection_evidence']['route']=='non_targeted_exploration']
    other=[e for e in selected if e['selection_evidence']['route']!='non_targeted_exploration'];result=[]
    for i in range(max(len(exploration),len(other))):
        if i<len(other):result.append(other[i])
        if i<len(exploration):result.append(exploration[i])
    return result


def mark_seen(registry,rows):
    history=registry.setdefault('seen_postings',{});prior_keys=set(history);seen=set();new=known=0
    for row in rows:
        key=hashlib.sha256(row['identity'].encode()).hexdigest()
        prior=history.get(key);row['previously_seen_in_this_registry']=key in prior_keys
        if key not in seen:
            if key in prior_keys:known+=1
            else:new+=1
        seen.add(key)
        old_hash=prior.get('description_sha256','') if prior else ''
        new_hash=row.get('description_sha256','')
        row['description_changed_since_prior_observation']=bool(key in prior_keys and old_hash and new_hash and old_hash!=new_hash)
        row['first_observed_run']=prior.get('first_run') if prior else registry['run']
        history[key]={'first_run':row['first_observed_run'],'last_run':registry['run'],'observed_at':row.get('retrieved_at',''),'description_sha256':new_hash or old_hash}
    # Bounded history: loss of old entries is explicit, never market-wide novelty.
    dropped=max(0,len(history)-50000)
    if dropped:
        for key in sorted(history,key=lambda k:history[k]['last_run'])[:dropped]:del history[key]
    return {'first_observed_by_registry':new,'previously_observed':known,'history_entries':len(history),'history_evicted':dropped,
            'note':'First observation by this saved registry, not proof of a new vacancy. Missing/expired artifacts reset history.'}
