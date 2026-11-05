"""Synthetic integration tests; never open or evaluate the quarantined review packet."""
import copy
import csv
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch

from tools.publish_experimental import publish,main,WARNING
from tools.prepare_blind_review import split_records,write_packet,digest
from tools.diagnose_triage import assert_not_sealed

ROOT=Path(__file__).resolve().parents[1]
NOW=datetime(2026,11,5,tzinfo=timezone.utc)


def fake_record(i=0,geo='dc_dmv',**kw):
    return dict(title='Synthetic Program Assistant',company=f'Synthetic employer {i%3}',location='Washington, DC',
                url=f'https://example.org/job/{i}',source='synthetic',source_id=str(i),geography=geo,
                description='Required: bachelor degree. Duties: research, writing, events and office support.',**kw)


def capture(rows=None):
    return {'kind':'SYNTHETIC TEST AUDIT, NOT VACANCIES','captured_at':NOW.isoformat(),
            'records':rows or [fake_record(),{**fake_record(1),'description':''}],
            'stats':{'synthetic':{'status':'ok','errors':[]}}}


def script(name):
    text=(ROOT/'.github/workflows/discovery.yml').read_text()
    step=text.split('      - name: '+name+'\n',1)[1].split('\n      - ',1)[0]
    return '\n'.join(line[10:] for line in step.split('        run: |\n',1)[1].splitlines())+'\n'


class ExperimentalPublisherTests(unittest.TestCase):
    def test_every_artifact_and_unranked_bytes_preserved_without_network(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);src=p/'unranked.json';src.write_text(json.dumps(capture()));before=src.read_bytes()
            with patch('requests.sessions.Session.request',side_effect=AssertionError('No network to rank')):
                status=publish(src,p/'ranking',as_of=NOW)
            self.assertEqual(src.read_bytes(),before);self.assertTrue(status['input_preserved'])
            names={x.name for x in (p/'ranking').iterdir()}
            self.assertEqual(names,{'README.md','run-status.json','experimental-ranked-brief.md','experimental-ranked-brief.json',
                'experimental-ranked-brief-audit.csv','experimental-ranked-brief-shortlist.csv','experimental-review-queue.json','experimental-review-queue.csv'})
            ranked=json.loads((p/'ranking/experimental-ranked-brief.json').read_text())
            self.assertEqual(ranked['counts']['retained'],2);self.assertTrue(ranked['experimental'])
            self.assertEqual(ranked['validation_status'],'not_independently_validated')
            self.assertEqual(ranked['unranked_audit']['sha256'],hashlib.sha256(before).hexdigest())
            self.assertIn(WARNING,(p/'ranking/experimental-ranked-brief.md').read_text())
            for filename in ['experimental-ranked-brief-shortlist.csv','experimental-ranked-brief-audit.csv','experimental-review-queue.csv']:
                with (p/'ranking'/filename).open(newline='') as f:rows=list(csv.DictReader(f))
                self.assertTrue(all(r['validation_status']=='not_independently_validated' and r['experimental']=='True' for r in rows))
            queue=json.loads((p/'ranking/experimental-review-queue.json').read_text())
            self.assertTrue(any(r['inspection_route']=='missing_description' for r in queue['records']))

    def test_partial_sources_are_not_hidden(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);data=capture();data['stats']['synthetic']={'status':'failed','errors':['http_429']}
            src=p/'source.json';src.write_text(json.dumps(data));status=publish(src,p/'rank',as_of=NOW)
            self.assertEqual(status['status'],'completed_with_partial_sources')
            self.assertIn('partial data',(p/'rank/experimental-ranked-brief.md').read_text())

    def test_failed_ranker_leaves_source_and_failure_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);src=p/'source.json';src.write_text(json.dumps(capture()));before=src.read_bytes()
            with patch('tools.publish_experimental.engine.triage',side_effect=RuntimeError('synthetic fault')):
                with self.assertRaises(RuntimeError):publish(src,p/'rank',as_of=NOW)
            self.assertEqual(src.read_bytes(),before)
            status=json.loads((p/'rank/run-status.json').read_text());self.assertEqual(status['status'],'failed')
            self.assertEqual(status['error_type'],'RuntimeError');self.assertTrue(status['input_preserved'])

    def test_missing_and_bad_input_fail_without_inventing_results(self):
        with tempfile.TemporaryDirectory() as td,redirect_stdout(io.StringIO()):
            p=Path(td)
            for suffix,data in [('missing',None),('bad','not json'),('schema',json.dumps({'records':[],'stats':{'x':[]}}))]:
                src=p/(suffix+'.json')
                if data is not None:src.write_text(data)
                result=main(['--input',str(src),'--output-dir',str(p/suffix)])
                self.assertEqual(result,1);self.assertTrue((p/suffix/'run-status.json').exists())
                self.assertFalse((p/suffix/'experimental-ranked-brief.json').exists())

    def test_geography_and_verification_survive_publication(self):
        rows=[{**fake_record(0),'location':'Camp Springs, Maryland'},
              {**fake_record(1),'location':'Maryland'},
              {**fake_record(2),'location':'Arlington, Texas'}]
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);src=p/'input.json';src.write_text(json.dumps(capture(rows)));publish(src,p/'rank',as_of=NOW)
            byloc={r['location']:r for r in json.loads((p/'rank/experimental-ranked-brief.json').read_text())['audit']}
            self.assertEqual(byloc['Camp Springs, Maryland']['geography'],'dc_dmv')
            self.assertEqual(byloc['Maryland']['decision'],'investigate')
            self.assertEqual(byloc['Arlington, Texas']['decision'],'location_review')
            self.assertTrue(all(not r['verification']['employer_active_verified'] for r in byloc.values()))


class BlindedReviewIsolationTests(unittest.TestCase):
    def test_prior_aliases_and_same_text_excluded_before_selection(self):
        prior=fake_record(0);prior['original_description_sha256']=digest(prior['description'])
        records=[prior,{**fake_record(10),'title':prior['title'],'company':prior['company']},
                 {**fake_record(11),'description':prior['description']},
                 {**fake_record(12),'company':'Different employer','description':'Different actual text'}]
        blind,diag,summary=split_records(records,[prior],1,9)
        self.assertEqual(summary['unseen_count'],1)
        self.assertEqual(len(blind)+len(diag),1)

    def test_packet_allowlist_hides_all_engine_fields_and_leaves_labels_blank(self):
        rows=[{**fake_record(2),'rank':1,'score':99,'decision':'SECRET_RECOMMENDATION','recommendation':'SECRET',
               'supported_experience':['MODEL_EVIDENCE'],'qualifications':{'text':'MODEL_SUMMARY'}}]
        with tempfile.TemporaryDirectory() as td:
            seal=write_packet(rows,Path(td)/'packet')
            packet=json.loads((Path(td)/'packet/packet.json').read_text())
            text=json.dumps(packet)
            for value in ['SECRET','MODEL_EVIDENCE','MODEL_SUMMARY','"score"','"rank"','"decision"']:
                self.assertNotIn(value,text)
            self.assertEqual(packet['records'][0]['original_description'],rows[0]['description'])
            with (Path(td)/'packet/labels.csv').open(newline='') as f:labels=list(csv.DictReader(f))
            self.assertTrue(all(v=='' for k,v in labels[0].items() if k!='review_id'))
            self.assertFalse(seal['model_evaluated_on_packet']);self.assertFalse(seal['labels_present'])
            self.assertFalse(seal['independent_review_available'])
            self.assertIn('Exact',packet['background'].replace('exact','Exact'))

    def test_packet_and_diagnostics_disjoint_stable_and_input_immutable(self):
        records=[{**fake_record(i,geo=['dc_dmv','us_remote','elsewhere'][i%3]),'description':str(i),'title':f'Synthetic Role {i}'} for i in range(60)]
        before=copy.deepcopy(records)
        blind,diag,_=split_records(records,[],30,9)
        self.assertEqual(len(blind),30);self.assertEqual(len(diag),30)
        self.assertFalse({r['url'] for r in blind}&{r['url'] for r in diag})
        self.assertEqual([r['url'] for r in blind],[r['url'] for r in split_records(list(reversed(records)),[],30,9)[0]])
        self.assertEqual(records,before)

    def test_location_variants_cannot_straddle_review_and_diagnostics(self):
        a=fake_record(0);b={**a,'url':'https://example.org/alias','geography':'elsewhere','source_id':'other'}
        blind,diag,summary=split_records([a,b],[],1,9)
        self.assertEqual(len(blind)+len(diag),1)
        self.assertEqual(summary['excluded_prior_or_duplicate'],1)

    def test_existing_review_or_human_labels_never_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'packet';write_packet([fake_record()],p)
            (p/'labels.csv').write_text('review_id,fit_label\nR001,plausible\n')
            with self.assertRaises(ValueError):write_packet([fake_record()],p)
            self.assertIn('plausible',(p/'labels.csv').read_text())

    def test_diagnostic_tool_refuses_sealed_urls(self):
        row=fake_record();seal={'records':[{'url_sha256':digest(row['url'])}]}
        with self.assertRaises(ValueError):assert_not_sealed([row],[seal])
        assert_not_sealed([fake_record(1)],[seal])
        pairseal={'records':[{'url_sha256':'other','employer_title_sha256':digest('synthetic employer 0|synthetic program assistant') }]}
        with self.assertRaises(ValueError):assert_not_sealed([row],[pairseal])

    def test_ranker_rules_are_frozen_and_real_review_not_a_fixture(self):
        self.assertEqual(hashlib.sha256((ROOT/'career_engine/triage.py').read_bytes()).hexdigest(),
                         '22eeaea408d245af83dc476ea734286f1ac773cbbb7a5b7f0d71d2e3e0fa6266')
        # Metadata commitments only; never load packet bodies or run predictions.
        seals=[json.loads((ROOT/'review_holdout/issue9'/p).read_text()) for p in ('seal.json','current-seal.json')]
        for seal in seals:
            self.assertFalse(seal['model_evaluated_on_packet']);self.assertFalse(seal['labels_present'])
        self.assertNotIn('review_holdout',str(ROOT/'tests/fixtures'))


class ManualWorkflowTests(unittest.TestCase):
    def test_gate_is_explicit_dc_only_and_off_by_default(self):
        text=(ROOT/'.github/workflows/discovery.yml').read_text()
        block=text.split('      experimental_ranking:',1)[1].split('      query_budget:',1)[0]
        self.assertIn('type: boolean',block);self.assertIn('default: false',block)
        step=text.split('      - name: Publish experimental ranking',1)[1].split('      - name: Upload report',1)[0]
        self.assertIn("inputs.source_mode == 'dc'",step);self.assertIn('inputs.experimental_ranking',step)
        self.assertIn('always()',step);self.assertIn('!cancelled()',step)
        self.assertNotRegex(text,r'(?m)^\s*(schedule:|- cron:)')
        self.assertIn('if: always()',text.split('      - name: Upload report',1)[1])
        self.assertIn('output/experimental-ranking/',text)
        for extension in ('json','csv','md'):self.assertIn('output/dc-source-audit.'+extension,text)

    def test_rank_step_cannot_rerun_collection_or_enable_paid_mode(self):
        code=script('Publish experimental ranking from the existing DC audit')
        self.assertIn('tools.publish_experimental',code)
        self.assertNotIn('tools.audit_dc_sources',code);self.assertNotIn('career_engine --sources',code)
        self.assertNotIn('BRAVE',code);self.assertNotIn('allow-paid',code)
        subprocess.run(['/bin/bash','-n'],input=code,text=True,check=True)

    def test_execute_actual_workflow_bash_once_from_synthetic_audit(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);(p/'bin').mkdir();fixture=p/'fixture.json';fixture.write_text(json.dumps(capture()))
            log=p/'commands.log';stub=p/'bin/python'
            stub.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$COMMAND_LOG"\n'
                'if [[ "$*" == *"tools.audit_dc_sources"* ]]; then\n'
                'mkdir -p output\ncp "$FIXTURE_AUDIT" output/dc-source-audit.json\n'
                'exit 0\nfi\nexec "$REAL_PYTHON" "$@"\n');stub.chmod(0o755)
            env={**os.environ,'PATH':str(p/'bin')+os.pathsep+os.environ['PATH'],'PYTHONPATH':str(ROOT),
                 'REAL_PYTHON':sys.executable,'COMMAND_LOG':str(log),'FIXTURE_AUDIT':str(fixture),
                 'MODE':'dc','BUDGET':'6','EXTRACT_BUDGET':'0','SOURCE_PAGES':'6',
                 'DC_DETAIL_BUDGET':'20','INSPECTION_SEED':'9','ALLOW_PAID_SEARCH':'false',
                 'GITHUB_STEP_SUMMARY':str(p/'summary.md')}
            subprocess.run(['/bin/bash','-c',script('Run discovery')],cwd=p,env=env,check=True,capture_output=True)
            before=(p/'output/dc-source-audit.json').read_bytes()
            subprocess.run(['/bin/bash','-c',script('Publish experimental ranking from the existing DC audit')],cwd=p,env=env,check=True,capture_output=True)
            self.assertEqual((p/'output/dc-source-audit.json').read_bytes(),before)
            commands=log.read_text().splitlines()
            self.assertEqual(sum('tools.audit_dc_sources' in x for x in commands),1)
            self.assertEqual(sum('tools.publish_experimental' in x for x in commands),1)
            self.assertIn('EXPERIMENTAL',(p/'summary.md').read_text())
            self.assertTrue((p/'output/experimental-ranking/experimental-review-queue.csv').exists())


if __name__=='__main__':unittest.main()
