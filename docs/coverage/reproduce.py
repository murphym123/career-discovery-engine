"""Offline arithmetic for Issue 19. No fetching, ranking or engine changes.

Run: python3 docs/coverage/reproduce.py --check
Inputs are minimized, dated observations, not synthetic vacancy fixtures.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent


def load(name):
    return json.loads((ROOT / 'data' / name).read_text())


def canonical(url):
    """Only known URL aliases; retain requisition paths and substantive queries."""
    value = urlsplit(url)
    host = (value.hostname or '').lower()
    if host == 'boards.greenhouse.io':
        host = 'job-boards.greenhouse.io'
    query = sorted((k, v) for k, v in parse_qsl(value.query)
                   if not k.startswith('utm_') and k not in ('source', 'ref', 'gh_src', 'lever-source'))
    return urlunsplit(('https', host, value.path.rstrip('/'), urlencode(query), ''))


def distinct(records):
    """Connected identity groups: exact canonical URL OR exact public ATS ID.

    Titles are never merge keys. This deduplicates listings, not proven vacancies.
    """
    parents = list(range(len(records)))
    aliases = {}

    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    for i, row in enumerate(records):
        keys = [('url', canonical(row['url']))]
        if row.get('posting_identity'):
            keys.append(('id', json.dumps(row['posting_identity'], sort_keys=True)))
        for key in keys:
            if key in aliases:
                parents[root(i)] = root(aliases[key])
            aliases[key] = i
    groups = defaultdict(list)
    for i, row in enumerate(records):
        groups[root(i)].append(row)
    return list(groups.values())


def norm(text):
    return re.sub(r'[^a-z0-9]+', ' ', text.lower()).strip()


def summarize_inventory(audits):
    """Unknown is not zero; no global completeness from only a preview window."""
    measured = [x for x in audits if x['observed_inventory_items'] is not None]
    return {'endpoints': len(audits), 'endpoints_with_observed_count': len(measured),
            'unknown_inventory_count': len(audits) - len(measured),
            'explicit_zero_endpoints': sum(x['observed_inventory_items'] == 0 for x in measured)}


def compute():
    engine = load('engine-observations.json')
    rows = load('engine-regional-metadata.json')
    local = [r for r in rows if r['engine_geography'] == 'dc_dmv']
    latent = [r for r in rows if r['diagnostic_metro_locality']]
    groups = distinct(local)
    possible = defaultdict(list)
    for r in local:
        possible[(norm(r['company']), norm(r['title']))].append(r['url'])
    inventories = load('independent-inventory-summary.json')
    audit = load('source-audit.json')
    benchmarks = load('benchmarks.json')
    examples = load('qualification-examples.json')
    return {
        'schema_version': 'coverage-research-calculations/v1',
        'engine': {
            'raw_sightings': sum(x['raw_sightings'] for x in engine['sources']),
            'regional_label_sightings': len(local), 'distinct_regional_listing_identities': len(groups),
            'regional_identities_described': sum(any(r['description_observed'] for r in g) for g in groups),
            'regional_description_fraction': sum(any(r['description_observed'] for r in g) for g in groups) / len(groups),
            'regional_employer_name_groups': len({norm(r['company']) for r in local}),
            'possible_same_employer_title_groups': sum(len(set(v)) > 1 for v in possible.values()),
            'regional_expired_by_explicit_date': sum(r['expired_by_explicit_date'] for r in local),
            'regional_without_explicit_closing_date': sum(not r['closing_at'] for r in local),
            'regional_title_remote_signals': sum(r['remote_in_title'] for r in local),
            'additional_retrieved_metro_locality_sightings': len(latent),
            'additional_metro_locality_identities': len(distinct(latent)),
            'additional_metro_locality_described': sum(r['description_observed'] for r in latent),
            'additional_metro_employer_name_groups': len({norm(r['company']) for r in latent}),
            'geography_diagnostic_union_identities': len(distinct(local + latent)),
            'latent_locations': dict(Counter(r['location'] for r in latent)),
            'local_quarantined': sum(r['quarantined'] for r in local),
        },
        'sample': {
            **summarize_inventory(audit),
            'strata': dict(Counter(r['stratum'] for r in audit)),
            'prior_access_controls': sum(r['prior_control'] for r in audit),
            'new_checks': sum(not r['prior_control'] for r in audit),
            'known_engine_endpoints': sum(r['engine_discovery'] == 'known_endpoint' for r in audit),
            'known_engine_endpoints_excluding_controls': sum(r['engine_discovery'] == 'known_endpoint' and not r['prior_control'] for r in audit),
            'public_inventory_endpoints': len(inventories),
            'inventory_items': sum(r['observed_records'] for r in inventories),
            'offered_regional_labels': sum(r['observed_regional_labels'] for r in inventories),
            'exact_engine_overlap_items': sum(r['engine_exact_matches'] for r in inventories),
            'possible_company_title_overlap_items': sum(r['possible_company_title_matches'] for r in inventories),
            'protected_records_not_inspected': sum(r['quarantined_records'] for r in inventories),
            'descriptions_reviewed': len(examples),
            'explicit_early_preferred_or_assistant_track': sum(r['early_career_judgment'] in ('explicit_early_career_preferred', 'explicit_early_career_assistant_track') for r in examples),
            'plausible_early_with_degree_constraints': sum(r['early_career_judgment'] == 'plausible_0_2_with_degree_constraints' for r in examples),
            'above_0_2_despite_title': sum(r['early_career_judgment'].startswith(('not_0_2', 'above_0_2')) for r in examples),
        },
        'incompatible_ratio_illustrations_NOT_coverage': [
            {'benchmark': b['id'], 'engine_listing_identities': len(groups), 'benchmark_postings': b['postings'],
             'arithmetic_percent_only': 100 * len(groups) / b['postings'], 'market_coverage_estimate': None}
            for b in benchmarks if b.get('postings') is not None
        ],
        'market_coverage': {'all_current_paid_vacancies': None, 'relevant_early_career_vacancies': None,
                            'reason': 'No coeval enumerated compatible population or probability inclusion design.'},
    }


def manifest():
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT / 'data').glob('*')) if p.name != 'manifest.json'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Verify frozen calculations and input hashes; no network')
    args = parser.parse_args()
    result = compute()
    if args.check:
        if result != json.loads((ROOT / 'calculations.json').read_text()):
            raise SystemExit('Calculations differ from committed results')
        if manifest() != load('manifest.json'):
            raise SystemExit('Research input hash mismatch')
        print('Frozen observations, input hashes and calculations agree; no market percentage inferred.')
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
