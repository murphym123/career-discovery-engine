"""Write a conspicuous manual-run handoff, independent of experimental ranking."""
import argparse
import json
import os
from pathlib import Path
from career_engine.collection_health import collection_health,health_markdown


def summarize(base_path,expanded_path=None):
    base=json.loads(Path(base_path).read_text())
    data=json.loads(Path(expanded_path).read_text()) if expanded_path else base
    result={'collection_health':collection_health(data['stats']),'records_retained':len(data['records']),
        'original_source_health':collection_health(base['stats']),
        'note':'A successful ATS stage cannot erase original-source failures. No ranking or candidate evaluation.'}
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base',required=True);p.add_argument('--expanded');p.add_argument('--output',required=True)
    a=p.parse_args();out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    try:
        result=summarize(a.base,a.expanded);text=health_markdown(result['collection_health'])+f"\nFull unranked sightings retained: {result['records_retained']}.\n\n{result['note']}\n"
    except (OSError,ValueError,KeyError) as exc:
        result={'collection_health':{'state':'failed'},'error':type(exc).__name__,'note':'Expected collection artifact missing/invalid; inspect the failed stage and preserved artifacts.'}
        text='## COLLECTION HANDOFF FAILED\n\n'+result['note']+'\n'
    out.write_text(json.dumps(result,indent=2)+'\n');out.with_suffix('.md').write_text(text)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with Path(os.environ['GITHUB_STEP_SUMMARY']).open('a') as f:f.write(text)
    state=result['collection_health']['state']
    if state!='ok':print('::'+('error' if state=='failed' else 'warning')+'::'+text.splitlines()[0].lstrip('# '))
    return 1 if state=='failed' else 0


if __name__=='__main__':raise SystemExit(main())
