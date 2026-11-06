"""Description retrieval allocation, not candidate ranking or job exclusion."""
import hashlib
from collections import Counter
from career_engine.inspection import identity,family


def detail_plan(records,candidates,budget,seed=0,market=None):
    local=market.local_label if market else 'dc_dmv'
    def region(row):
        return 0 if row['geography']==local else 1 if row['geography']=='regional_worksite_unknown' else 2
    # Conditional extra slots apply only when observed regional metadata remains
    # uninspected. The total is bounded by 2x the requested budget and 60 pages;
    # transport request/byte/time limits still apply to every request.
    remaining_local=sum(region(r)<2 for r in candidates)
    maximum=min(60,budget+min(budget,remaining_local))
    counts=Counter(r['board_key'] for r in records if r.get('description') and region(r)<2)
    functions=Counter(family(r) for r in records if r.get('description') and region(r)<2)
    pending=list(candidates);plan=[]
    hashed=lambda r:hashlib.sha256((str(seed)+'|'+identity(r)).encode()).hexdigest()
    for slot in range(min(maximum,len(pending))):
        # Extra slots never expand into a wholly unscoped remote backlog.
        pool=[r for r in pending if slot<budget or region(r)<2]
        if not pool:break
        explore=slot%5==4
        row=min(pool,key=lambda r:(region(r),counts[r['board_key']],0 if explore else functions[family(r)],hashed(r)))
        plan.append({'identity':identity(row),'stratum':'regional_broad_exploration' if explore else 'regional_board_diversity',
                     'conditional_extra_slot':slot>=budget})
        counts[row['board_key']]+=1;functions[family(row)]+=1;pending.remove(row)
    return plan


def description_completeness(rows,market=None):
    local=market.local_label if market else 'dc_dmv'
    def counts(group):
        regional=[r for r in group if r['geography']==local]
        uncertain=[r for r in group if r['geography'] in ('regional_worksite_unknown','unknown','remote_eligibility_unknown','us_remote')]
        described=sum(bool(r.get('description')) for r in regional)
        return {'local_index_records':len(regional),'local_with_description':described,
            'local_untruncated_descriptions':sum(bool(r.get('description')) and not r.get('description_truncated',False) for r in regional),
            'local_truncated_descriptions':sum(bool(r.get('description')) and bool(r.get('description_truncated',False)) for r in regional),
            'local_missing_description':len(regional)-described,
            'local_description_fraction':described/len(regional) if regional else None,
            'uncertain_or_remote_index_records':len(uncertain),
            'uncertain_or_remote_with_description':sum(bool(r.get('description')) for r in uncertain),
            'note':'Observed metadata denominator only; text may be truncated or omit mandatory requirements. Not market coverage or job suitability.'}
    return {'total':counts(rows),'boards':{key:counts([r for r in rows if r['board_key']==key]) for key in sorted({r['board_key'] for r in rows})}}
