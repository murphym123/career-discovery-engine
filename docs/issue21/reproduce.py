"""Offline calculations from minimized actual observations; no live requests."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent

def read(name):return json.loads((ROOT/name).read_text())

def counts(rows):
    regional=[r for r in rows if r['geography']=='dc_dmv']
    return {'regional_listing_identities':len({r['identity'] for r in regional}),
            'regional_with_description':sum(r['description_observed'] for r in regional),
            'regional_untruncated_descriptions':sum(r['description_observed'] and not r['description_truncated'] for r in regional),
            'regional_boards':len({r['board_key'] for r in regional})}

def compute():
    records=read('paired-regional-records.json');discovery=read('discovery-and-inventory.json');ind=read('independent-records.json');result={}
    for arm in ['prior','new']:
        boards=discovery[arm]['boards']
        result[arm]={**counts(records[arm]),'index_identities':sum(b['unique'] for b in boards),
            'attempted_boards':len(boards),'retrieval_requests':sum(b['requests'] for b in boards)}
        # Global transport includes free discovery/robots/directory requests, recorded separately.
        result[arm]['total_logical_requests']=read('input-provenance.json')['transport_requests'][arm]
        result[arm]['regional_not_matched_to_five_source_capture']=sum(r['geography']=='dc_dmv' and r['duplicate_status']=='not_matched_to_five_source_capture' for r in records[arm])
        result[arm]['possible_regional_crossposts']=sum(r['geography']=='dc_dmv' and r['duplicate_status']=='possible_crosspost_review' for r in records[arm])
    old_local={r['identity'] for r in records['prior'] if r['geography']=='dc_dmv'}
    new_local={r['identity'] for r in records['new'] if r['geography']=='dc_dmv'}
    new_by_id={r['identity']:r for r in records['new']}
    result['change']={'additional_regional_labels':len(new_local-old_local),'no_longer_labelled_regional':len(old_local-new_local),
        'previous_regional_record_still_retained':all(k in new_by_id for k in old_local),
        'net_regional_labels':len(new_local)-len(old_local),
        'net_described_regional':result['new']['regional_with_description']-result['prior']['regional_with_description'],
        'new_clear_generalist_0_2_examples_established_in_operational_arm':0,
        'verified_paid_current_vacancy_gain':None}
    result['independent_inventory_check']={**counts(ind),'all_index_records':len(ind),
        'all_descriptions':sum(r['description_observed'] for r in ind),
        'exact_matches_to_prior_production_capture':len(read('production-overlap.json')['exact_matches']),
        'boards':{key:counts([r for r in ind if r['board_key']==key]) for key in sorted({r['board_key'] for r in ind})},
        'plausible_early_career_examples_with_material_conditions':sum(r['assessment'].startswith('plausible_early') for r in read('qualification-review.json'))}
    result['limits']='Listing and description counts, not confirmed current paid jobs or market coverage. Purposeful diagnostic boards are not independent human validation.'
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args();data=compute()
    if args.check:
        assert data==read('metrics.json'),'Metric drift'
        expected=read('manifest.json')
        assert all(hashlib.sha256((ROOT/p).read_bytes()).hexdigest()==digest for p,digest in expected.items()),'Input hash mismatch'
        print('Actual observation metrics and evidence hashes agree; no live requests.')
    else:print(json.dumps(data,indent=2))

if __name__=='__main__':main()
