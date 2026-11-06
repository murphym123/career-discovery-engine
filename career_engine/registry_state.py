"""Lossless registry validation and atomic publication; observations stay separate."""
import hashlib
import copy
import json
import os
from pathlib import Path
import tempfile

from career_engine.ats import board_from_url
from career_engine.ats_registry import empty_registry


def strict_registry(data):
    """Reject invalid state instead of silently dropping boards or history.

    Older v1 registries may omit later optional fields; retain all supplied fields.
    Bounds belong to discovery writers, not a destructive restore sanitizer.
    """
    if not isinstance(data,dict) or type(data.get('schema_version')) is not int or data['schema_version']!=1:
        raise ValueError('registry_schema')
    for key,default in empty_registry().items():
        if key not in data and key in ('boards','run','query_offset','index_pages','index_counts','host_not_before','unsupported'):
            raise ValueError('registry_missing_field')
        value=data.get(key,default)
        if type(value) is not type(default):raise ValueError('registry_field_type')
        if isinstance(default,int) and value<0:raise ValueError('registry_negative_cursor')
    result={**empty_registry(),**data}
    for key,board in result['boards'].items():
        if not isinstance(board,dict):raise ValueError('registry_board_type')
        parsed=board_from_url(board.get('url'))
        if not parsed or parsed['key']!=key or any(board.get(k)!=parsed[k] for k in ('key','provider','token')):
            raise ValueError('registry_board_identity')
        if not isinstance(board.get('evidence'),list) or any(not isinstance(e,dict) for e in board['evidence']):
            raise ValueError('registry_board_evidence')
        checked=board.get('last_checked_run')
        if checked is not None and (type(checked) is not int or checked<0):raise ValueError('registry_board_cursor')
    for field in ('index_pages','index_counts'):
        if any(type(v) is not int or v<0 for v in result[field].values()):raise ValueError('registry_index_cursor')
    if any(not isinstance(v,str) for v in result['host_not_before'].values()):raise ValueError('registry_cooldown')
    if any(not isinstance(v,dict) for v in result['unsupported']):raise ValueError('registry_unsupported')
    if any(not isinstance(v,dict) for v in result['employer_sites'].values()):raise ValueError('registry_employers')
    for value in result['seen_postings'].values():
        if not isinstance(value,dict) or any(type(value.get(k)) is not int or value[k]<0 for k in ('first_run','last_run')):
            raise ValueError('registry_posting_history')
    if any(not isinstance(v,list) or any(not isinstance(s,dict) for s in v) for v in result['source_status_snapshots'].values()):
        raise ValueError('registry_source_history')
    return result


def parse_json(body):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('duplicate_json_key')
            result[key]=value
        return result
    def nonfinite(value):raise ValueError('nonfinite_json_value')
    return json.loads(body,object_pairs_hook=pairs,parse_constant=nonfinite)


def read_registry(path):
    state=copy.deepcopy(strict_registry(parse_json(Path(path).read_bytes())))
    # Keep Issue 21's legacy iCIMS URL promotion, retaining the original lead.
    from career_engine.ats_registry import register
    for lead in state['unsupported']:
        if board_from_url(lead.get('url','')):
            register(state,lead['url'],lead.get('evidence',{'kind':'legacy_observed_public_link'}))
    return state


def atomic_bytes(path,body):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    name=None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.'+path.name+'.',delete=False) as f:
            name=f.name;f.write(body);f.flush();os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if name and os.path.exists(name):os.unlink(name)


def atomic_json(path,data):
    atomic_bytes(path,(json.dumps(data,indent=2,ensure_ascii=False,allow_nan=False)+'\n').encode())


def save_registry(path,data):
    atomic_json(path,strict_registry(data))


def seal_snapshot(registry_path,folder,run_id,run_attempt):
    """Stage a complete independent copy for upload only after pipeline success."""
    body=Path(registry_path).read_bytes();state=strict_registry(parse_json(body))
    folder=Path(folder)
    atomic_bytes(folder/'ats-registry.json',body)
    manifest={'schema_version':'ats-registry-snapshot/v1','complete':True,'run_id':str(run_id),
              'run_attempt':int(run_attempt),'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest(),
              'boards':len(state['boards']),'seen_postings':len(state['seen_postings'])}
    atomic_json(folder/'registry-snapshot.json',manifest)
    return manifest


def verify_snapshot(folder,run_id,run_attempt,allow_legacy=False):
    folder=Path(folder)
    if (folder/'ats-registry.json').is_symlink() or (folder/'registry-snapshot.json').is_symlink():raise ValueError('snapshot_symlink')
    body=(folder/'ats-registry.json').read_bytes()
    state=strict_registry(parse_json(body))
    manifest_path=folder/'registry-snapshot.json'
    if not manifest_path.exists():
        if not allow_legacy:raise ValueError('snapshot_manifest_missing')
        # Successful pre-Issue-23 runs used unsealed v1 files. Full JSON + strict
        # lossless validation permits migration without discarding known history.
        return body,'legacy_v1_structural_validation'
    m=parse_json(manifest_path.read_bytes())
    if not isinstance(m,dict) or m.get('schema_version')!='ats-registry-snapshot/v1' or m.get('complete') is not True:
        raise ValueError('snapshot_manifest')
    expected={'run_id':str(run_id),'run_attempt':int(run_attempt),'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest(),
              'boards':len(state['boards']),'seen_postings':len(state['seen_postings'])}
    if any(m.get(k)!=v for k,v in expected.items()):raise ValueError('snapshot_integrity')
    return body,'sha256_manifest_and_structure'
