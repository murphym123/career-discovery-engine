"""Cold-start publication discovery in a US city; no employer registry required.

This portable entry point uses global public publication mechanisms. The existing
manual DC workflow additionally preserves its five-source ecosystem fixture.
"""
import argparse
import json
from pathlib import Path
from career_engine.ats import PublicTransport
from career_engine.markets import city_market
from tools.expand_ats import expand


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--city',required=True);p.add_argument('--state',required=True)
    p.add_argument('--output-dir',required=True);p.add_argument('--board-budget',type=int,default=10)
    p.add_argument('--request-budget',type=int,default=100);p.add_argument('--sitemap-budget',type=int,default=2)
    p.add_argument('--seed-queries',type=int,default=3);p.add_argument('--extra-details',type=int,default=10)
    a=p.parse_args(argv)
    if not 1<=a.board_budget<=100 or not 1<=a.request_budget<=300 or not 0<=a.sitemap_budget<=5 or not 0<=a.seed_queries<=20 or not 0<=a.extra_details<=30:p.error('Invalid collection budget')
    try:market=city_market(a.city,a.state)
    except ValueError as e:p.error(str(e))
    out=Path(a.output_dir);out.mkdir(parents=True,exist_ok=True)
    base=out/'empty-source-baseline.json';registry=out/'registry.json';cold=not registry.exists()
    if not base.exists():base.write_text(json.dumps({'kind':'EMPTY FIRST-RUN BASELINE; NO SEED EMPLOYERS','records':[],'stats':{},'market':market.asdict()},indent=2)+'\n')
    data=expand(base,registry,out/'sources',out/'collection.json',a.board_budget,a.seed_queries,
        transport=PublicTransport(max_requests=a.request_budget),extra_details=a.extra_details,strategy='regional',
        employer_budget=0,sitemap_budget=a.sitemap_budget,market=market)
    result={'market':market.asdict(),'cold_start':cold,'seed_employers':0,'known_boards_before_run':0 if cold else 'restored from this output directory',
            'coverage':data['coverage'],'logical_requests':len(data['transport']['requests'])}
    (out/'run-summary.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n');print(json.dumps(result))
    return 0 if any(s['status']!='failed' for s in data['discovery']['boards']) or any(s.get('status')=='ok' for s in data['additional_source_stats'].values()) else 1


if __name__=='__main__':raise SystemExit(main())
