"""Explicit source-health signals; a source failure never means zero vacancies."""


def collection_health(stats):
    sources={}
    for name,counts in stats.items():
        state='failed' if counts.get('status')=='failed' else 'partial' if counts.get('errors') or counts.get('status')=='partial_failure' else 'ok'
        sources[name]={'state':state,'signal':{'failed':'red','partial':'yellow','ok':'green'}[state],
            'records':counts.get('unique',0),'errors':counts.get('errors',[]),
            'note':'Successful bounded collection is not a complete publisher inventory.' if state=='ok' else 'Access/collection failure; not evidence of zero vacancies.'}
    state='failed' if sources and all(s['state']=='failed' for s in sources.values()) else 'partial' if any(s['state']!='ok' for s in sources.values()) else 'ok'
    return {'state':state,'label':{'failed':'COLLECTION FAILED','partial':'PARTIAL COLLECTION','ok':'SUCCESSFUL BOUNDED COLLECTION'}[state],
            'sources':sources,'inventory_completeness':'Not inferred from successful requests; see source-scoped coverage ledger.'}


def health_markdown(health):
    lines=['## '+health['label'],'',health['inventory_completeness'],'',
        '| Source | Signal | Records retained | Errors |','|---|---|---:|---|']
    for name,s in health['sources'].items():
        errors='; '.join(map(str,s['errors'])).replace('|','\\|').replace('\n',' ')
        lines.append(f"| {name} | {s['signal']} / {s['state']} | {s['records']} | {errors} |")
    return '\n'.join(lines)+'\n'
