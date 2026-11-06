"""Issue 23: batch retrieval, state integrity and credential-aware handoff."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from career_engine.ats import board_from_url,retrieve_board,normalize_posting
from career_engine.ats_registry import empty_registry,register
from career_engine.registry_state import save_registry,read_registry,seal_snapshot,verify_snapshot
from career_engine.regional_usajobs import collect_usajobs
from tools.registry_state import restore,LEGACY_WORKFLOW_BLOB
from tests.test_ats import Session,Response,transport,gh_item

ROOT=Path(__file__).resolve().parents[1]
BOARD=board_from_url('https://job-boards.greenhouse.io/syntheticcivic')
BASE='https://boards-api.greenhouse.io/v1/boards/syntheticcivic'


class GreenhouseBatchTests(unittest.TestCase):
    def test_one_list_includes_all_descriptions_offices_and_raw_evidence(self):
        item=gh_item(location='Multiple offices',content='&amp;lt;p&amp;gt;Policy &amp;amp; events&amp;lt;/p&amp;gt;',
                     offices=[{'id':8,'name':'HQ','location':'Washington, DC'}])
        session=Session({BASE+'/jobs':Response({'jobs':[item,gh_item(2,'London, UK',content='<p>Other job.</p>')],'meta':{'total':2}}),BASE:Response({'name':'Civic'})})
        rows,stats=retrieve_board(transport(session),BOARD)
        self.assertEqual(len(session.calls),2);self.assertEqual(session.calls[0][1],{'content':'true'})
        self.assertEqual(rows[0]['description'],'Policy & events');self.assertEqual(rows[0]['geography'],'dc_dmv')
        self.assertEqual(rows[0]['raw_provider_record'],item);self.assertEqual(rows[0]['offices'],item['offices'])
        self.assertTrue(stats['inventory']['metadata_complete']);self.assertTrue(stats['inventory']['descriptions_complete'])
        self.assertEqual(stats['description_mode'],'content_list');self.assertEqual(stats['descriptions'],2)

    def test_html_entities_preserve_literal_angle_brackets_and_boundaries(self):
        import html
        body='<p>Use &lt;formula&gt; &amp; research</p><ul><li>One</li><li>Two</li></ul>'
        for encoded in (body,html.escape(body),html.escape(html.escape(body))):
            row=normalize_posting(BOARD,gh_item(content=encoded))
            self.assertEqual(row['description'],'Use <formula> & research One Two')
            self.assertEqual(row['raw_provider_record']['content'],encoded)

    def test_oversized_response_falls_back_and_preserves_all_index_rows(self):
        def cb(url,params):
            if url==BASE+'/jobs' and params:return Response(raw=b'x'*8_000_001)
            if url==BASE+'/jobs':return Response({'jobs':[gh_item(n) for n in range(1,5)]})
            if '/jobs/' in url:return Response(gh_item(int(url.rsplit('/',1)[1]),content='A real synthetic body.'))
            return Response({'name':'Civic'})
        session=Session(callback=cb);t=transport(session);rows,stats=retrieve_board(t,BOARD,detail_budget=2)
        self.assertEqual(len(rows),4);self.assertEqual(stats['descriptions'],2)
        self.assertEqual(stats['description_mode'],'metadata_fallback_bounded_details')
        self.assertEqual(stats['status'],'partial_failure');self.assertIn('content_list_response_too_large',stats['errors'])
        self.assertTrue(stats['inventory']['metadata_complete']);self.assertFalse(stats['inventory']['descriptions_complete'])
        self.assertEqual(t.requests[0]['error'],'response_too_large');self.assertTrue(stats['capped'])

    def test_denial_schema_network_and_global_limits_do_not_trigger_fallback(self):
        for response,kw in [(Response({},403),{}),(Response({},429),{}),(Response({'bad':[]}),{}),
                            (Response(raw=b'x'*8_000_001),{'max_bytes':1000})]:
            session=Session({BASE+'/jobs':response});rows,stats=retrieve_board(transport(session,**kw),BOARD)
            self.assertFalse(rows);self.assertEqual(stats['status'],'failed');self.assertEqual(len(session.calls),1)

    def test_no_detail_budget_or_failed_fallback_is_never_empty_success(self):
        session=Session(callback=lambda url,p:Response(raw=b'x'*8_000_001) if p else Response({},429))
        rows,stats=retrieve_board(transport(session),BOARD,detail_budget=0)
        self.assertFalse(rows);self.assertEqual(stats['status'],'failed');self.assertEqual(len(session.calls),2)

    def test_missing_bodies_fill_only_missing_and_keep_successful_body_on_failure(self):
        session=Session({BASE+'/jobs':Response({'jobs':[gh_item(content='Already observed'),gh_item(2)]}),BASE:Response({'name':'Civic'}),BASE+'/jobs/2':Response({},403)})
        rows,stats=retrieve_board(transport(session),BOARD,detail_budget=2)
        self.assertEqual(rows[0]['description'],'Already observed');self.assertEqual(len(rows),2)
        self.assertEqual(stats['status'],'partial_failure');self.assertEqual(session.calls[-1][0],BASE+'/jobs/2')
        self.assertFalse(any(c[0]==BASE+'/jobs/1' for c in session.calls))


class RegistryIntegrityTests(unittest.TestCase):
    def state(self):
        state=empty_registry();register(state,BOARD['url'],{'kind':'public_link','url':'https://example.org/'})
        state['seen_postings']={'a':{'first_run':1,'last_run':2,'description_sha256':'old'}}
        state['source_status_snapshots']={'source':[{'state':'inaccessible','errors':['http_403']}]}
        return state

    def test_atomic_failed_serialization_replace_and_validation_preserve_prior_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'state.json';state=self.state();save_registry(path,state);prior=path.read_bytes()
            with patch('career_engine.registry_state.os.replace',side_effect=OSError('disk')):
                with self.assertRaises(OSError):save_registry(path,{**state,'run':3})
            self.assertEqual(path.read_bytes(),prior);self.assertEqual(len(list(Path(td).iterdir())),1)
            for bad in [{**state,'seen_postings':{'a':[]}}, {**state,'boards':{BOARD['key']:{'url':'https://evil.example'}}}, {**state,'run':True}]:
                with self.assertRaises(ValueError):save_registry(path,bad)
                self.assertEqual(path.read_bytes(),prior)

    def test_save_and_seal_retain_partial_status_and_full_history(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);state=self.state();save_registry(p/'state',state);seal_snapshot(p/'state',p/'snapshot',12,1)
            body,level=verify_snapshot(p/'snapshot',12,1)
            self.assertEqual(json.loads(body),state);self.assertEqual(level,'sha256_manifest_and_structure')
            self.assertEqual(read_registry(p/'state'),state)
            (p/'snapshot/ats-registry.json').write_text(json.dumps({**state,'run':99}))
            with self.assertRaises(ValueError):verify_snapshot(p/'snapshot',12,1)

    def test_restore_skips_failed_running_corrupt_and_missing_artifacts_then_uses_latest_valid(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);state=self.state();save_registry(p/'active',state);prior=(p/'active').read_bytes()
            snapshots=p/'snapshots';snapshots.mkdir()
            for run_id in (7,6):seal_snapshot(p/'active',snapshots/str(run_id),run_id,1)
            (snapshots/'7/registry-snapshot.json').write_text('{}')
            runs=[{'databaseId':i,'status':'in_progress' if i==10 else 'completed','conclusion':'failure' if i==9 else 'success','createdAt':str(i).zfill(2),'attempt':1} for i in (6,9,8,7,10)]
            downloads=[]
            def runner(*args):
                if args[:2]==('run','list'):return json.dumps(runs)
                if args[:2]==('run','download'):
                    run_id=args[2];downloads.append(int(run_id))
                    if run_id=='8':raise subprocess.CalledProcessError(1,['gh'])
                    shutil.copytree(snapshots/run_id,args[-1],dirs_exist_ok=True);return ''
                raise AssertionError(args)
            result=restore(p/'active',p/'diag','owner/repo','main','11',runner)
            self.assertEqual(downloads,[8,7,6]);self.assertEqual(result['state_restored_from'],6)
            self.assertEqual((p/'active').read_bytes(),prior);self.assertEqual(json.loads((p/'diag').read_text()),result)

    def test_all_failures_keep_active_state_and_record_failure(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);save_registry(p/'active',self.state());prior=(p/'active').read_bytes()
            def runner(*a):raise subprocess.CalledProcessError(1,['gh'])
            result=restore(p/'active',p/'diag','o/r','main','1',runner)
            self.assertEqual((p/'active').read_bytes(),prior);self.assertIsNone(result['state_restored_from'])
            self.assertIn('restore_failure',result)

    def test_legacy_migration_requires_known_producer_blob_not_just_missing_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);state=self.state();save_registry(p/'legacy',state)
            for blob,restored in [('bad',False),(LEGACY_WORKFLOW_BLOB,True)]:
                def runner(*a):
                    if a[:2]==('run','list'):return json.dumps([{'databaseId':4,'status':'completed','conclusion':'success','createdAt':'2026','headSha':'a'*40}])
                    if a[:2]==('run','download'):shutil.copyfile(p/'legacy',Path(a[-1])/'ats-registry.json');return ''
                    if a[0]=='api':return blob
                    raise AssertionError(a)
                result=restore(p/'active',p/'diag','o/r','main','5',runner)
                self.assertEqual(result['status']=='restored',restored)
                if restored:self.assertEqual(json.loads((p/'active').read_text()),state)

    def test_duplicate_or_nonfinite_json_and_invalid_local_state_are_diagnosed(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)
            for body in ('{"boards":{},"boards":{}}','{"value":NaN}'):
                (p/'active').write_text(body)
                with self.assertRaises(ValueError):read_registry(p/'active')
            with self.assertRaises(ValueError):restore(p/'active',p/'diag','o/r','main','2',lambda *a:'[]')
            self.assertEqual(json.loads((p/'diag').read_text())['restore_failure'],'existing_registry_invalid')

    def test_report_write_failure_preserves_registry_but_retains_observations(self):
        from tools.expand_ats import expand
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);save_registry(p/'registry',self.state());prior=(p/'registry').read_bytes()
            (p/'base.json').write_text(json.dumps({'records':[],'stats':{}}))
            with patch('tools.expand_ats.write_ledger',side_effect=OSError('disk')):
                with self.assertRaises(OSError):expand(p/'base.json',p/'registry',p/'out',p/'combined.json',board_budget=1,seed_queries=0,transport=transport(Session()))
            self.assertEqual((p/'registry').read_bytes(),prior);self.assertTrue((p/'out/ats-source-audit.json').exists())

    def test_legacy_icims_lead_promotion_preserves_original_evidence(self):
        state=empty_registry();lead={'url':'https://careers-example.icims.com/jobs/search','evidence':{'kind':'legacy_link'}}
        state['unsupported']=[lead]
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'registry';save_registry(p,state);loaded=read_registry(p)
            self.assertIn('icims:careers-example',loaded['boards']);self.assertEqual(loaded['unsupported'],[lead])

    def test_workflow_snapshot_save_requires_success_but_reports_always_upload(self):
        text=(ROOT/'.github/workflows/discovery.yml').read_text()
        for name in ('Validate and seal completed registry snapshot','Save discovered ATS registry for future manual runs'):
            step=text.split('      - name: '+name,1)[1].split('      - name:',1)[0]
            self.assertIn("if: success() && inputs.source_mode == 'dc' && inputs.expanded_ats",step)
            self.assertNotIn('always()',step)
        self.assertIn('output/registry-restore.json',text)
        self.assertIn('path: data/registry-snapshot/',text)


class USAJobsRegionalTests(unittest.TestCase):
    ENV={'USAJOBS_API_KEY':'TEST_SECRET_KEY','USAJOBS_USER_AGENT':'test@example.invalid','USAJOBS_TERMS_ACCEPTED':'true'}
    def item(self,i):
        return {'MatchedObjectId':str(i),'MatchedObjectDescriptor':{'PositionTitle':'Unusual Role',
                'OrganizationName':'Federal agency','PositionLocationDisplay':'Washington, DC',
                'PositionURI':f'https://www.usajobs.gov/job/{i}', 'UserArea':{'Details':{'JobSummary':'Public duties'}}}}
    def response(self,total=101,items=None):
        return Response({'SearchResult':{'SearchResultCountAll':total,'SearchResultItems':items or [self.item(1)]}})

    def test_missing_config_or_terms_makes_zero_requests_and_precise_report(self):
        for env,configured in [({},False),({'USAJOBS_API_KEY':'x'},False),({k:v for k,v in self.ENV.items() if k!='USAJOBS_TERMS_ACCEPTED'},True)]:
            with patch.dict(os.environ,env,clear=True):
                session=Session();rows,stats=collect_usajobs(session=session,pause=lambda _:None)
            self.assertFalse(rows);self.assertFalse(session.calls);self.assertEqual(stats['status'],'not_collected')
            self.assertEqual(stats['configured'],configured);self.assertIn('setup_requirements',stats)

    def test_configured_uses_existing_regional_query_keeps_raw_and_never_logs_headers(self):
        with patch.dict(os.environ,self.ENV,clear=True):
            session=Session(callback=lambda u,p:self.response(101));rows,stats=collect_usajobs(1,session,pause=lambda _:None)
        self.assertEqual(len(rows),1);self.assertTrue(stats['capped']);self.assertEqual(stats['status'],'ok')
        url,params,kwargs=session.calls[0];self.assertEqual(params['Radius'],35);self.assertEqual(params['ResultsPerPage'],100)
        self.assertEqual(kwargs['headers']['Authorization-Key'],self.ENV['USAJOBS_API_KEY']);self.assertFalse(kwargs['allow_redirects'])
        self.assertEqual(rows[0]['raw_provider_record'],self.item(1));self.assertEqual(rows[0]['identity'],'usajobs:1')
        self.assertFalse(stats['inventory']['metadata_complete'])
        text=json.dumps({'rows':rows,'stats':stats})
        for secret in (self.ENV['USAJOBS_API_KEY'],self.ENV['USAJOBS_USER_AGENT']):self.assertNotIn(secret,text)

    def test_later_failure_preserves_rows_and_stops_without_redirect_or_retry(self):
        for status in (302,403,429):
            with patch.dict(os.environ,self.ENV,clear=True):
                session=Session(callback=lambda u,p:self.response() if p['Page']==1 else Response({},status))
                rows,stats=collect_usajobs(3,session,pause=lambda _:None)
            self.assertEqual(len(rows),1);self.assertEqual(len(session.calls),2);self.assertEqual(stats['status'],'partial_failure')

    def test_oversized_payload_fails_without_mock_jobs(self):
        with patch.dict(os.environ,self.ENV,clear=True):
            session=Session(callback=lambda u,p:Response(raw=b'x'*8_000_001));rows,stats=collect_usajobs(2,session,pause=lambda _:None)
        self.assertFalse(rows);self.assertEqual(stats['status'],'failed');self.assertEqual(len(session.calls),1)
        self.assertEqual(stats['transport']['requests'][0]['error'],'response_too_large')

    def test_regional_handoff_is_dc_only_and_keeps_feed_and_federal_rows(self):
        from tools.expand_ats import regional_candidates
        from career_engine.markets import DC_MARKET,city_market
        feed={'identity':'rss:1','source':'impactopportunity','url':'https://example.org/1','location':'Washington, DC'}
        federal={'identity':'usajobs:1','source':'usajobs','url':'https://www.usajobs.gov/job/1','location':'Washington, DC'}
        with patch('tools.expand_ats.retrieve_impact',return_value=([feed],{'status':'ok'})), \
             patch('tools.expand_ats.discover_jazz_sitemap',return_value=[]), \
             patch('career_engine.regional_usajobs.collect_usajobs',return_value=([federal],{'status':'ok'})) as collector:
            data=regional_candidates(transport(Session()),empty_registry(),{'records':[]},usajobs_pages=2)
            self.assertEqual(data['records'],[feed,federal]);collector.assert_called_once_with(2)
            collector.reset_mock()
            data=regional_candidates(transport(Session()),empty_registry(),{'records':[]},market=city_market('Denver','CO'))
            collector.assert_not_called();self.assertEqual(data['records'],[feed])

    def test_legacy_expansion_never_calls_optional_collector(self):
        from tools.expand_ats import expand
        with tempfile.TemporaryDirectory() as td,patch('career_engine.regional_usajobs.collect_usajobs') as collector:
            p=Path(td);(p/'base.json').write_text(json.dumps({'records':[],'stats':{}}))
            expand(p/'base.json',p/'registry',p/'out',p/'combined.json',board_budget=1,seed_queries=0,transport=transport(Session()))
            collector.assert_not_called()

    def test_config_report_has_api_source_ledger_even_without_credentials(self):
        from career_engine.publication_audit import build_ledger
        with patch.dict(os.environ,{},clear=True):rows,stats=collect_usajobs()
        ledger=build_ledger({'records':[],'stats':{}},supplemental={'records':rows,'stats':{'usajobs':stats}})
        source=next(s for s in ledger['sources'] if s.get('source')=='usajobs')
        self.assertEqual(source['source_kind'],'documented_api');self.assertEqual(source['retrieval_state'],'not_collected')
        self.assertFalse(source['configured']);self.assertEqual(source['url'],'https://data.usajobs.gov/api/search')


if __name__=='__main__':unittest.main()
