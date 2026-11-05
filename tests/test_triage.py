"""Business-risk tests. Synthetic rules cases are not real-vacancy accuracy data."""
import copy
import csv
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from career_engine.triage import PROFILE, evaluate, requirements, triage
from tools.rank_jobs import main, load_records, write_outputs
from tools.benchmark_triage import compare, stress

ROOT=Path(__file__).resolve().parents[1]
NOW=datetime(2026,11,5,8,tzinfo=timezone.utc)


def synthetic(title='Program Assistant',description='Required: bachelor degree. Duties: research, writing, events and program administration.',**overrides):
    return {'title':title,'company':'Synthetic test employer','location':'Washington, DC','url':'https://example.org/jobs/test',
            'source':'arbitrary_future_ats','source_id':'test','description':description,**overrides}


class RequirementSafetyTests(unittest.TestCase):
    def test_preferred_legal_credential_does_not_block_ba_applicant(self):
        r=evaluate(synthetic(description='Duties: political research and writing. Required: bachelor degree. JD strongly preferred; no professional license required.'),as_of=NOW)
        self.assertNotIn('law_degree',[g['kind'] for g in r['qualifications']['mandatory_gaps']])
        self.assertTrue(any(x['kind']=='law_degree' and x['modality']=='preferred' for x in r['qualifications']['requirements']))

    def test_preferred_section_survives_flattened_html(self):
        rs=requirements("Basic Qualifications Bachelor's degree and writing skills. Preferred Qualifications Master's degree in policy. 5+ years of professional experience.")
        self.assertTrue(all(x['modality']=='preferred' for x in rs if x['kind'] in ('advanced_degree','experience')))

    def test_required_jd_not_erased_by_other_preference(self):
        rs=requirements('Qualifications JD required. Masters preferred. Minimum five years of experience required.')
        self.assertTrue(any(x['kind']=='law_degree' and x['modality']=='required' for x in rs))
        self.assertTrue(any(x.get('minimum_years')==5 and x['modality']=='required' for x in rs))

    def test_long_jd_preference_separate_from_mandatory_ba(self):
        rs=requirements('Qualifications Bachelor degree required JD and/or other graduate training, with demonstrated experience in legal and policy research and writing, strongly preferred.')
        self.assertTrue(any(x['kind']=='law_degree' and x['modality']=='preferred' for x in rs))

    def test_years_ranges_words_digits_and_repeated_numbers(self):
        for phrase,lo,hi in [('two (2) years of experience',2,2),('2–4 years of experience',2,4),
                             ('five to seven fundraising years',5,7),('ten or more professional years',10,10)]:
            rs=requirements('Required: '+phrase+'.')
            self.assertTrue(any(x.get('minimum_years')==lo and x.get('maximum_years')==hi for x in rs),phrase)

    def test_senior_title_alone_is_not_a_disqualification(self):
        j=synthetic(title='Senior Office Administrator',description='Required: office skills and writing. Duties: administration, records, events and proofreading.')
        r=evaluate(j,as_of=NOW)
        self.assertEqual(r['decision'],'reasonable_application')
        self.assertFalse(r['qualifications']['mandatory_gaps'])

    def test_benign_title_cannot_hide_mandatory_professional_credentials(self):
        for description in ['Required: JD and active bar membership. Duties: research and policy writing.',
                            'Required: active CPA and minimum ten years of financial management experience. Duties: operations and donor programs.',
                            'Required: registered nurse with nursing license. Duties: research and community programs.']:
            self.assertEqual(evaluate(synthetic(title='Research Associate',description=description),as_of=NOW)['decision'],'qualification_gap')

    def test_non_nursing_job_at_nurses_union(self):
        r=evaluate(synthetic(description='Duties: organizing, campaign planning and writing. Required: organizing experience. Not a nursing license position.'),as_of=NOW)
        self.assertFalse(r['qualifications']['mandatory_gaps'])

    def test_employing_attorneys_is_not_requiring_attorney(self):
        r=evaluate(synthetic(description='The committee employs attorneys and economists. Duties: administrative support, writing and events. Required: office skills.'),as_of=NOW)
        self.assertFalse(any(x['kind']=='law_degree' for x in r['qualifications']['risks']))

    def test_clearance_obtainable_vs_current(self):
        j=synthetic(description='Duties: political research and writing. Active Secret clearance or ability to obtain one is required.')
        r=evaluate(j,as_of=NOW)
        self.assertFalse(r['qualifications']['mandatory_gaps'])
        self.assertTrue(any(x['kind']=='active_clearance' for x in r['qualifications']['risks']))
        j['description']='Required: current Top Secret clearance with SCI access. Duties: political research and writing.'
        self.assertEqual(evaluate(j,as_of=NOW)['decision'],'qualification_gap')

    def test_equivalence_is_preserved_not_assumed(self):
        r=evaluate(synthetic(description='Required: bachelor degree and two years administrative or equivalent professional experience. Duties: research, writing and events.'),as_of=NOW)
        self.assertEqual(r['decision'],'reasonable_application')
        self.assertTrue(r['qualifications']['compatible_experience_ranges'])
        self.assertTrue(any(x['kind']=='equivalence_review' for x in r['qualifications']['risks']))
        self.assertIn('not_confirmed',r['qualifications']['status'])

    def test_trainable_workflow_not_mandatory_domain_mastery(self):
        r=evaluate(synthetic(description='Required: research and writing. Tax workflow can be learned through cross-training. Duties: office records and events.'),as_of=NOW)
        self.assertFalse(r['qualifications']['mandatory_gaps'])
        self.assertTrue(any(x['trainable'] for x in r['qualifications']['preferences_or_trainable']))

    def test_exact_years_never_invented_from_profile(self):
        r=evaluate(synthetic(description='Required: minimum three years of relevant experience. Duties: research and writing.'),as_of=NOW)
        self.assertIn('duration',r['qualifications']['compatible_experience_ranges'][0]['assessment'])
        self.assertTrue(all('support' in e for e in r['supported_experience']))
        self.assertNotIn('years',PROFILE['capabilities']['research'])

    def test_missing_description_is_unknown_not_unfit(self):
        r=evaluate(synthetic(title='Unfamiliar Fellow',description=''),as_of=NOW)
        self.assertEqual(r['decision'],'investigate')
        self.assertEqual(r['qualifications']['status'],'unknown')
        self.assertFalse(r['qualifications']['mandatory_gaps'])

    def test_csv_false_and_truncation(self):
        r=evaluate(synthetic(qualifications_complete='false',description_truncated='true'),as_of=NOW)
        self.assertEqual(r['decision'],'investigate')
        self.assertIn('truncated_description',[x['kind'] for x in r['qualifications']['risks']])

    def test_no_inferred_ideology_salary_or_employer_size_exclusion(self):
        a=evaluate(synthetic(),as_of=NOW)
        b=evaluate(synthetic(company='Unfamiliar ideological micro-employer',salary='$20,000'),as_of=NOW)
        self.assertEqual(a['decision'],b['decision']);self.assertEqual(a['review_priority'],b['review_priority'])

    def test_board_observation_cannot_be_supplied_as_independent_verification(self):
        r=evaluate(synthetic(source='globaljobs',employer_active_verified=True),as_of=NOW)
        self.assertFalse(r['verification']['employer_active_verified'])
        self.assertEqual(r['verification']['status'],'board_observed_not_independently_verified')

    def test_dates_and_ambiguous_geography(self):
        self.assertEqual(evaluate(synthetic(closing_at='2026-11-01'),as_of=NOW)['decision'],'closed_by_source_date')
        r=evaluate(synthetic(location='Maryland'),as_of=NOW)
        self.assertEqual(r['decision'],'investigate')
        self.assertEqual(r['geography'],'regional_worksite_unknown')
        self.assertNotEqual(evaluate(synthetic(location='Remote, US'),as_of=NOW)['decision'],'location_review')


class PipelineAndEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records=load_records(ROOT/'docs/issue7/evaluation-input.json')
        cls.labels=json.loads((ROOT/'docs/issue7/review-labels.json').read_text())['records']

    def test_every_actual_record_retained_and_sources_preserved(self):
        out=triage(self.records,30,200,20,17,NOW)
        self.assertEqual(out['counts']['retained'],327)
        self.assertEqual(out['counts']['detailed_analyses'],95)
        self.assertEqual(set(out['counts']['sources']),{'senate','unionjobs','globaljobs','onthinktanks','workfornonprofits'})
        self.assertEqual(out['cost']['model_calls'],0)

    def test_bounded_work_explores_low_signal_without_mutating_inputs(self):
        jobs=[synthetic(source_id=str(i),title='Program Assistant' if i<70 else 'Unfamiliar Title',company=str(i),url=f'https://example.org/{i}') for i in range(100)]
        saved=copy.deepcopy(jobs)
        out=triage(jobs,15,10,8,17,NOW)
        self.assertEqual(jobs,saved);self.assertEqual(out['counts']['detailed_analyses'],10)
        self.assertEqual(out['counts']['retained'],100)
        self.assertTrue(any(r['inspection_route']=='score_independent_exploration' for r in out['audit']))
        self.assertTrue(any(r['inspection_route']=='detailed_budget_pending' and r['decision']=='investigate' for r in out['audit']))
        self.assertEqual(out['shortlist'],triage(list(reversed(jobs)),15,10,8,17,NOW)['shortlist'])

    def test_zero_budget_and_unscored_exploration(self):
        out=triage(self.records,15,0,12,17,NOW)
        self.assertEqual(out['counts']['detailed_analyses'],0)
        self.assertFalse(out['shortlist'])
        self.assertEqual(len(out['exploration_queue']),12)
        self.assertEqual(out['counts']['retained'],327)

    def test_distinct_requisitions_and_sources_never_merge_by_title(self):
        jobs=[synthetic(source='first',source_id='1'),synthetic(source='second',source_id='2'),synthetic(source='first',source_id='1')]
        out=triage(jobs,15,20,5,17,NOW)
        self.assertEqual(out['counts']['retained'],3)
        self.assertEqual(len({r['identity'] for r in out['audit']}),3)
        self.assertTrue(all(r['possible_duplicate']['count']==3 for r in out['audit']))
        self.assertTrue(all(r['possible_duplicate']['action']=='flag_only_no_merge' for r in out['audit']))

    def test_actual_input_metadata_and_original_hashes_match_prior_capture(self):
        prior={f"{r['source']}:{r['source_id']}":r for r in json.loads((ROOT/'docs/issue5/records.json').read_text())['records']}
        self.assertEqual(len(prior),len(self.records))
        for row in self.records:
            before=prior[row['identity']]
            for field in ('title','company','location','url'):
                self.assertEqual(row.get(field,''),before.get(field,''))
            if row['description']:
                self.assertEqual(row['original_description_sha256'],before['description_sha256'])
                self.assertEqual(row['description_representation'],'analyst_nonverbatim_abstract')

    def test_holdout_is_disjoint_and_labels_do_not_enter_evaluator(self):
        a={x['identity'] for x in self.labels if x['partition']=='development'}
        b={x['identity'] for x in self.labels if x['partition']=='holdout'}
        self.assertEqual(len(a),27);self.assertEqual(len(b),55);self.assertFalse(a&b)
        self.assertTrue(all('label' not in r and 'partition' not in r for r in self.records))
        self.assertEqual(hashlib.sha256((ROOT/'docs/issue7/review-labels.json').read_bytes()).hexdigest(),'cfb3067029fb194677b58cbc9c516f67e1007f137a4a9bf4f3b0931fb62108e8')

    def test_real_corpus_comparison_is_reproducible(self):
        expected=json.loads((ROOT/'docs/issue7/comparison.json').read_text())
        actual,_=compare(self.records,self.labels,datetime.fromisoformat(expected['as_of']))
        self.assertEqual(actual['results'],expected['results'])
        self.assertEqual(actual['false_negatives'],expected['false_negatives'])

    def test_future_ats_and_no_network_or_ai_dependency(self):
        with patch('requests.sessions.Session.request',side_effect=AssertionError('network forbidden')):
            result=triage([synthetic(source='future_employer_ats')],15,200,10,0,NOW)
        self.assertEqual(result['audit'][0]['decision'],'reasonable_application')
        self.assertEqual(result['cost'],{'network_calls':0,'model_calls':0,'paid_services_enabled':False})

    def test_actionable_report_and_all_record_exports(self):
        with tempfile.TemporaryDirectory() as td:
            report=Path(td)/'brief.md'
            result=triage([synthetic(),synthetic(source_id='missing',description=''),synthetic(source_id='invalid',url='')],15,200,10,0,NOW)
            write_outputs(result,report)
            text=report.read_text()
            for phrase in ['Supported experience','Qualifications','Potential advantage','Gaps/verification','Status/freshness','No posting in this output is independently employer-verified active','Exploration']:
                self.assertIn(phrase,text)
            with (Path(td)/'brief-audit.csv').open(newline='') as f:rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),3)
            self.assertIn('invalid_record',{r['decision'] for r in rows})
            self.assertEqual(len(json.loads(report.with_suffix('.json').read_text())['audit']),3)

    def test_cli_is_offline_and_reproducible(self):
        with tempfile.TemporaryDirectory() as td,redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--input',str(ROOT/'docs/issue7/evaluation-input.json'),'--as-of',NOW.isoformat(),'--output',str(Path(td)/'brief.md')]),0)
            self.assertTrue((Path(td)/'brief-audit.csv').exists())

    def test_null_fields_and_malformed_urls_remain_in_audit(self):
        rows=[synthetic(company=None,source=None,source_id=None),synthetic(title=None),synthetic(url='https://')]
        report=triage(rows,15,200,10,0,NOW)
        self.assertEqual(report['counts']['retained'],3)
        self.assertEqual(sum(r['decision']=='invalid_record' for r in report['audit']),2)

    def test_integrated_runner_keeps_duplicate_and_invalid_retrieved_sightings(self):
        from career_engine.__main__ import main as engine_main
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);input_file=root/'jobs.csv'
            jobs=[synthetic(),synthetic(),synthetic(title='')]
            with input_file.open('w',newline='') as f:
                w=csv.DictWriter(f,fieldnames=list(jobs[0]));w.writeheader();w.writerows(jobs)
            args=['engine','--sources','csv','--import-csv',str(input_file),'--db',str(root/'jobs.db'),
                  '--output',str(root/'legacy.md'),'--triage-output',str(root/'new.md')]
            with patch('sys.argv',args),redirect_stdout(io.StringIO()):self.assertEqual(engine_main(),0)
            report=json.loads((root/'new.json').read_text())
            self.assertEqual(report['counts']['retained'],3)
            self.assertEqual(sum(r['decision']=='invalid_record' for r in report['audit']),1)
            self.assertTrue((root/'legacy.md').exists())

    def test_organization_history_is_not_experience_requirement(self):
        r=evaluate(synthetic(description='Founded ten years ago. Duties: research, events and writing. Required: bachelor degree.'),as_of=NOW)
        self.assertFalse(any(x['kind']=='experience' for x in r['qualifications']['requirements']))

    def test_scale_retains_all_with_fixed_description_budget(self):
        rows=stress(self.records,sizes=(5000,))
        self.assertEqual(rows[0]['retained'],5000);self.assertEqual(rows[0]['analyzed'],200)
        self.assertEqual(rows[0]['cost']['model_calls'],0)


if __name__=='__main__':unittest.main()
