"""Offline matched allocation comparison using hash-verified real response snapshots.

Never performs network or candidate ranking. Capture snapshots separately through
permitted public collectors; this tool does not manufacture vacancies or labels.
"""
import argparse
import copy
import hashlib
import json
from datetime import datetime,timezone
from pathlib import Path

import requests
from career_engine.ats import PublicTransport
from tools.expand_ats import expand


class FrozenResponse:
    def __init__(self,entry,body):
        self.status_code=entry['status'];self.headers=entry['headers'];self.body=body;self.complete=entry['complete']
    def iter_content(self,size):
        for n in range(0,len(self.body),size):yield self.body[n:n+size]
        if not self.complete:raise requests.exceptions.ChunkedEncodingError('Original bounded response was incomplete')
    def close(self):pass


class FrozenSession:
    def __init__(self,manifest_path):
        self.path=Path(manifest_path);self.manifest=json.loads(self.path.read_text());self.responses={}
        self.collection_context={'mode':'recorded_response_replay','snapshot_collected_at':self.manifest.get('collected_at'),
            'source_observation_window':self.manifest.get('source_observation_window'),
            'timestamp_note':'Output/row timestamps describe reconstruction. Acquisition timestamps in the response manifest govern source freshness; no new network request is implied.'}
        root=self.path.parent.resolve()
        for entry in self.manifest['responses']:
            if not isinstance(entry.get('complete'),bool) or not isinstance(entry.get('headers'),dict):raise ValueError('Invalid response metadata')
            file=(root/entry['body_file']).resolve()
            if not file.is_relative_to(root):raise ValueError('Response payload outside capture directory')
            body=file.read_bytes()
            if hashlib.sha256(body).hexdigest()!=entry['sha256']:raise ValueError('Response payload hash mismatch')
            key=self.key(entry['url'],entry.get('params'))
            if key in self.responses:raise ValueError('Conflicting snapshots for one URL/parameter identity')
            self.responses[key]=(entry,body)
    @staticmethod
    def key(url,params=None):return url+'|'+json.dumps(params or {},sort_keys=True)
    def get(self,url,params=None,**kwargs):
        key=self.key(url,params)
        if key not in self.responses:raise AssertionError('Uncaptured URL; offline comparison never falls back to live retrieval: '+url)
        entry,body=self.responses[key];return FrozenResponse(entry,body)


def compare(base_path,registry_path,candidate_path,manifest_path,output_dir,board_budget=30,request_budget=180,extra_details=20):
    output_dir=Path(output_dir);output_dir.mkdir(parents=True,exist_ok=True)
    registry=json.loads(Path(registry_path).read_text());capture=json.loads(Path(candidate_path).read_text())
    session=FrozenSession(manifest_path)
    expected=session.manifest.get('baseline_sha256')
    actual=hashlib.sha256(Path(base_path).read_bytes()).hexdigest()
    if expected and expected!=actual:raise ValueError('Five-source baseline does not match response snapshot')
    as_of=datetime.fromisoformat(session.manifest['collected_at'].replace('Z','+00:00'))
    if as_of.tzinfo is None:as_of=as_of.replace(tzinfo=timezone.utc)
    result={'kind':'CONTROLLED OFFLINE REPLAY OF ACTUAL RETRIEVAL; NOT INDEPENDENT VALIDATION',
            'network_calls':0,'model_calls':0,'as_of':as_of.isoformat(),'baseline_sha256':actual,
            'budgets_each':{'boards':board_budget,'logical_requests':request_budget,'shared_extra_descriptions':extra_details},'arms':{}}
    for strategy in ('legacy','regional'):
        folder=output_dir/strategy;folder.mkdir(exist_ok=True);reg=folder/'registry.json'
        reg.write_text(json.dumps(copy.deepcopy(registry),indent=2,ensure_ascii=False)+'\n')
        transport=PublicTransport(session,max_requests=request_budget,pause=lambda _:None,max_seconds=600)
        data=expand(base_path,reg,folder/'ats',folder/'combined.json',board_budget,0,
                    transport=transport,extra_details=extra_details,strategy=strategy,candidate_capture=copy.deepcopy(capture),as_of=as_of)
        routes={}
        for board in data['discovery']['boards']:
            route=board['selection_evidence']['route'];routes[route]=routes.get(route,0)+1
        result['arms'][strategy]={'coverage':data['coverage'],'logical_requests':len(transport.requests),
            'boards_checked':len(data['discovery']['boards']),'boards_with_rows':sum(b['unique']>0 for b in data['discovery']['boards']),
            'selection_routes':routes,'bytes_observed':transport.bytes}
    (output_dir/'comparison.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base',required=True);p.add_argument('--registry',required=True)
    p.add_argument('--candidate-capture',required=True);p.add_argument('--responses',required=True)
    p.add_argument('--output-dir',required=True);p.add_argument('--board-budget',type=int,default=30)
    p.add_argument('--request-budget',type=int,default=180);p.add_argument('--extra-details',type=int,default=20)
    args=p.parse_args(argv)
    if not 1<=args.board_budget<=100 or not 1<=args.request_budget<=300 or not 0<=args.extra_details<=30:p.error('Invalid comparison budget')
    result=compare(args.base,args.registry,args.candidate_capture,args.responses,args.output_dir,args.board_budget,args.request_budget,args.extra_details)
    print(json.dumps(result));return 0


if __name__=='__main__':raise SystemExit(main())
