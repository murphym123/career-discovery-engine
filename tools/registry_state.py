"""Restore verified successful snapshots in isolation, or seal state for upload."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

# Immutable pre-Issue-23 workflow blob: the only unsealed migration format.
LEGACY_WORKFLOW_BLOB='8f43f1f9257547fa1dadb002cf7c2dddc849ac02'

from career_engine.registry_state import atomic_bytes,atomic_json,read_registry,verify_snapshot,seal_snapshot


def gh(*args):
    # Never publish stderr or exception command details; GH_TOKEN stays in env.
    return subprocess.run(['gh',*args],capture_output=True,text=True,check=True).stdout


def restore(registry_path,diagnostic_path,repository,branch,run_id,runner=gh):
    had_state=Path(registry_path).exists()
    result={'state_restored_from':None,'status':'not_restored','attempts':[],
            'existing_state_preserved':had_state,'search_limit':100,'branch':branch,
            'note':'Successful pipeline snapshots may contain explicitly partial collection. Artifact expiry remains possible.'}
    try:
        # Explicit filters plus independent checks; failed/cancelled/running runs
        # never become candidates, even if they uploaded an artifact under always().
        runs=json.loads(runner('run','list','-R',repository,'--workflow','discovery.yml','--branch',branch,
                               '--status','success','--limit','100','--json','databaseId,status,conclusion,createdAt,attempt,headSha'))
        if not isinstance(runs,list):raise ValueError('run_list_schema')
        runs=sorted(runs,key=lambda r:r.get('createdAt',''),reverse=True)
        for run in runs:
            prior=run.get('databaseId')
            if str(prior)==str(run_id) or run.get('status')!='completed' or run.get('conclusion')!='success':continue
            attempt={'run_id':prior}
            result['attempts'].append(attempt)
            try:
                if type(prior) is not int or prior<=0:raise ValueError('run_identity')
                with tempfile.TemporaryDirectory(prefix='ats-restore-') as folder:
                    runner('run','download',str(prior),'-R',repository,'-n','public-ats-registry','-D',folder)
                    legacy=False
                    if not (Path(folder)/'registry-snapshot.json').exists():
                        # Missing manifests from new producers cannot downgrade to legacy.
                        sha=run.get('headSha','')
                        if not isinstance(sha,str) or len(sha)!=40:raise ValueError('legacy_run_commit_missing')
                        blob=runner('api',f'repos/{repository}/contents/.github/workflows/discovery.yml?ref={sha}','--jq','.sha').strip()
                        legacy=blob==LEGACY_WORKFLOW_BLOB
                    body,verification=verify_snapshot(folder,prior,run.get('attempt',1),allow_legacy=legacy)
                    atomic_bytes(registry_path,body)
                attempt.update(status='restored',verification=verification)
                result.update(status='restored',state_restored_from=prior,verification=verification,existing_state_preserved=False)
                break
            except (OSError,ValueError,TypeError,subprocess.SubprocessError) as exc:
                attempt.update(status='rejected',reason=str(exc) if isinstance(exc,ValueError) else type(exc).__name__)
        if result['status']!='restored':result['restore_failure']='no_valid_successful_snapshot'
    except (OSError,ValueError,TypeError,subprocess.SubprocessError) as exc:
        result['restore_failure']='run_listing_failed_'+type(exc).__name__
    # If a known-good file exists, failed restore leaves it byte-for-byte intact.
    # Cold start is explicit. Any corrupt existing state stops the pipeline.
    invalid_local=False
    if Path(registry_path).exists():
        try:read_registry(registry_path)
        except (OSError,ValueError,TypeError):
            result['restore_failure']='existing_registry_invalid';invalid_local=True
    result['cold_start']=not Path(registry_path).exists()
    atomic_json(diagnostic_path,result)
    summary=os.getenv('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary,'a') as f:f.write('\n## ATS registry restore\n\n```json\n'+json.dumps(result,indent=2)+'\n```\n')
    if invalid_local:raise ValueError('existing_registry_invalid')
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('restore','seal'))
    p.add_argument('--registry',default='data/ats-registry.json');p.add_argument('--diagnostic',default='output/registry-restore.json')
    p.add_argument('--snapshot-dir',default='data/registry-snapshot')
    a=p.parse_args(argv)
    if a.action=='restore':result=restore(a.registry,a.diagnostic,os.environ['GITHUB_REPOSITORY'],os.environ['GITHUB_REF_NAME'],os.environ['GITHUB_RUN_ID'])
    else:result=seal_snapshot(a.registry,a.snapshot_dir,os.environ['GITHUB_RUN_ID'],os.environ.get('GITHUB_RUN_ATTEMPT','1'))
    print(json.dumps(result));return 0


if __name__=='__main__':raise SystemExit(main())
