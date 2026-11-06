"""Paired Greenhouse replay from shared real captures; --live explicitly acquires.

Raw captures remain local; the compact manifest/metrics are reviewable without
republishing job descriptions. Never touches ranking, registries or holdouts.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import types

import requests
from career_engine.ats import PublicTransport,board_from_url,retrieve_board,stamp
from tools.compare_discovery import FrozenSession,FrozenResponse


class CaptureSession:
    def __init__(self,folder):
        self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True)
        self.entries=[];self.cache={};self.session=requests.Session()
    def get(self,url,params=None,**kw):
        key=FrozenSession.key(url,params)
        if key not in self.cache:
            response=self.session.get(url,params=params,**kw)
            body=bytearray();complete=True
            try:
                for chunk in response.iter_content(65536):
                    body.extend(chunk)
                    if len(body)>8_000_000:complete=False;break
                entry={'url':url,'params':params or {},'status':response.status_code,'observed_at':stamp(),
                       'headers':{'Content-Type':response.headers.get('Content-Type','')},'complete':complete,
                       'body_file':f'{len(self.entries):03d}.body','sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body)}
            finally:response.close()
            self.entries.append(entry);self.cache[key]=(entry,bytes(body));(self.folder/entry['body_file']).write_bytes(body)
            (self.folder/'responses.json').write_text(json.dumps({'collected_at':stamp(),'responses':self.entries},indent=2)+'\n')
        return FrozenResponse(*self.cache[key])


def measure(manifest,before_ref,tokens,records_dir=None):
    before=types.ModuleType('before_ats')
    original=subprocess.run(['git','show',before_ref+':career_engine/ats.py'],check=True,capture_output=True).stdout
    exec(compile(original,'before_ats.py','exec'),before.__dict__)
    results={'before_ref':before_ref,'before_module_sha256':hashlib.sha256(original).hexdigest(),
             'budgets_each':{'max_requests':30,'max_bytes':64_000_000,'response_bytes':8_000_000,'detail_budget':3},'boards':{}}
    for token in tokens:
        board=board_from_url('https://job-boards.greenhouse.io/'+token);arms={};identities={}
        for name,collector in [('before',before.retrieve_board),('after',retrieve_board)]:
            t=PublicTransport(manifest,max_requests=30,pause=lambda _:None)
            rows,stats=collector(t,board,detail_budget=3)
            if records_dir:
                folder=Path(records_dir);folder.mkdir(parents=True,exist_ok=True)
                (folder/(token+'-'+name+'.json')).write_text(json.dumps({'records':rows,'stats':stats},indent=2)+'\n')
            local=[r for r in rows if r['geography']=='dc_dmv'];identities[name]={r['identity'] for r in rows}
            arms[name]={'records':len(rows),'regional_records':len(local),'regional_descriptions':sum(bool(r['description']) for r in local),
                        'descriptions':sum(bool(r['description']) for r in rows),'requests':len(t.requests),
                        'extra_detail_requests':sum('/jobs/' in r['url'] for r in t.requests),'bytes':t.bytes,
                        'status':stats['status'],'capped':stats['capped'],'errors':stats['errors'],'inventory':stats['inventory'],
                        'description_mode':stats.get('description_mode','metadata_and_bounded_details'),'request_log':t.requests}
        results['boards'][token]={**arms,'all_before_identities_retained':identities['before']<=identities['after']}
    return results


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--captures',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--before-ref',default='b0fbaabef2f3390fc36519bf6d09b1225d8aba5e')
    p.add_argument('--live',action='store_true');p.add_argument('--boards',default='nationalnursesunited,andurilindustries')
    a=p.parse_args(argv);tokens=a.boards.split(',')
    if len(tokens)!=2:p.error('Use exactly one modest and one oversized observed board')
    if a.live:
        session=CaptureSession(a.captures)
        # Existing 1-second pacing, host-denial and budgets apply during capture.
        for token in tokens:
            board=board_from_url('https://job-boards.greenhouse.io/'+token)
            t=PublicTransport(session,max_requests=30)
            retrieve_board(t,board,detail_budget=3)
            # Acquire the old metadata request and its bounded detail selection.
            # measure() reuses this shared cache, making no duplicate GETs.
        measure(session,a.before_ref,tokens)
    result=measure(FrozenSession(a.captures/'responses.json'),a.before_ref,tokens,a.captures/'unranked-arms')
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:{arm:{m:v for m,v in d.items() if m not in ('request_log','inventory')} for arm,d in b.items() if isinstance(d,dict)} for k,b in result['boards'].items()},indent=2))


if __name__=='__main__':main()
