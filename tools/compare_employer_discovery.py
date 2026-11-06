"""Offline regional scorecard from retained observational runs, never fit/recall."""
import argparse
import hashlib
import json
from pathlib import Path

from career_engine.registry_state import atomic_json


def summarize(employer_path,global_path,registry_path):
    paths=map(Path,(employer_path,global_path,registry_path));e_path,g_path,r_path=paths
    e=json.loads(e_path.read_bytes());g=json.loads(g_path.read_bytes());prior=json.loads(r_path.read_bytes())
    def counts(rows):
        local={r['identity']:r for r in rows if r.get('geography')=='dc_dmv'}
        return {'regional_posting_identities':len(local),'regional_with_original_descriptions':sum(bool(r.get('description')) for r in local.values()),
                'regional_board_groups':len({r['board_key'] for r in local.values()}),
                'first_observed_regional_in_saved_history':sum(not r.get('previously_seen_in_this_registry',False) for r in local.values()),
                'first_observed_described_regional_in_saved_history':sum(bool(r.get('description')) and not r.get('previously_seen_in_this_registry',False) for r in local.values())}
    e_boards={c['board_key'] for row in e['employers'] for c in row['channels'] if c['kind']=='supported_ats' and c['identity_verified']}
    g_boards={s['board_key'] for s in g['discovery']['boards']}
    channel_types={}
    for row in e['employers']:
        for c in row['channels']:channel_types[c['kind']]=channel_types.get(c['kind'],0)+1
    return {'schema_version':'employer-discovery-comparison/v1','design':'OBSERVATIONAL; two live windows, same starting saved registry; different allocation, no causal/market-recall claim',
            'input_sha256':{str(p.name):hashlib.sha256(p.read_bytes()).hexdigest() for p in (e_path,g_path,r_path)},
            'budgets':{'each_arm_total_requests':180,'each_arm_bytes':64_000_000,'each_arm_seconds':600,
                       'employer':'30 frozen frame entities; 120 discovery + 60 collection requests, 3 pages/employer, 15 boards, 2 details/board',
                       'global':'existing legacy expand mode; 3 seed queries, 15 boards, 3 per-board + 2 shared details; empty comparison base, five-source collectors not rerun'},
            'employer_first':{**e['metrics'],**counts(e['records']),
                              'requests':sum(len(t['requests']) for t in e['transport'].values()),'bytes':sum(t['bytes'] for t in e['transport'].values()),
                              'channel_types':channel_types},
            'global_board_first':{**counts(g['records']),'selected_boards':g['discovery']['selected_boards'],
                                  'inventories_attempted':len(g['discovery']['boards']),'failed_inventory_groups':sum(s['status']=='failed' for s in g['discovery']['boards']),
                                  'requests':len(g['transport']['requests']),'bytes':g['transport']['bytes'],
                                  'verified_frame_sites':None,'frame_site_metric_note':'Global mode does not resolve this external employer frame',
                                  'frame_board_groups_overlapping_verified_employer_edges':len(e_boards&g_boards)},
            'starting_registry':{'boards':len(prior['boards']),'run':prior['run']},
            'limits':'Frame entities, shared board groups, published worksite labels and vacancy/fit are different units. None of these finite windows identifies market-wide recall. Zero employer-arm inventories means not_collected, not no vacancies.'}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--employer',required=True);p.add_argument('--global-audit',required=True)
    p.add_argument('--registry',required=True);p.add_argument('--output',required=True);a=p.parse_args(argv)
    result=summarize(a.employer,a.global_audit,a.registry);atomic_json(a.output,result)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
