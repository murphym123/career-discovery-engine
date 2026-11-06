"""Discovery worksite evidence, separate from candidate ranking/eligibility."""
import re
from career_engine.discovery import geography


def worksite_evidence(row,market=None):
    raw=row.get('raw_locations') or [row.get('location','')]
    if not isinstance(raw,list):raw=[raw]
    parts=list(dict.fromkeys(p.strip() for value in raw for p in re.split(r'\s*[;|]\s*',str(value)) if p.strip()))
    classifier=market.label if market else geography
    local_label=market.local_label if market else 'dc_dmv'
    labels=[classifier(p) for p in parts]
    if not market:labels=['dc_dmv' if re.fullmatch(r'(?i)D\.?\s*C\.?',p) else g for p,g in zip(parts,labels)]
    local=[p for p,g in zip(parts,labels) if g==local_label]
    # Demonstrated provider error: a Montreal tour-guide title was tagged DC.
    # Only an explicit nonlocal title suffix triggers this conservative conflict
    # route; mentioning a foreign city in research duties is not a location gate.
    away=r'(?:Montreal|Montréal|Toronto|London|Paris|Brussels|Boston|New York|San Francisco|Los Angeles)'
    title=str(row.get('title',''))
    conflict=bool(local and classifier(title)!=local_label and re.search(r'(?:[-–—]\s*'+away+r'(?:\s+Opportunities|\s+Office)?\s*)$',title,re.I))
    remote=str(row.get('work_arrangement','')).lower()=='remote' or bool(re.search(r'(?i)\bremote\b',row.get('location','')))
    if conflict:
        kind='conflicting_title_and_provider_location';label='unknown'
    elif local:
        kind='remote_with_regional_label' if remote else 'regional_option_multiple_locations' if len(parts)>1 else 'single_regional_label'
        label=local_label
    else:
        # Classifying individual locations avoids joining Arlington TX to a
        # Virginia label elsewhere in a multi-location posting.
        combined=classifier(row.get('location',''))
        label=combined if combined!=local_label else ('us_remote' if 'us_remote' in labels else 'unknown')
        kind='remote_not_regional_worksite' if 'remote' in label else 'geography_unresolved' if label in ('unknown','regional_worksite_unknown') else 'outside_region'
    return {'market_id':market.identifier if market else 'dc-dmv','discovery_geography':label,'geography':label,
            'worksite_evidence':{'kind':kind,'offered_location_labels':parts,'regional_location_labels':local,
                'note':'Published labels, not a verified physical worksite or authorization to work remotely from the selected market.'}}
