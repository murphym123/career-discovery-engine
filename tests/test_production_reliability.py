"""Production failure regressions. Synthetic requests; actual capture QA is separate."""
import copy
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch
import requests

from tests.test_dc_sources import Session as DCSession,Response as DCResponse,fixture
from tests.test_ats import Session,Response,transport,gh_item
from tests.test_regional_discovery import jazz_html,html_response
from career_engine.dc_sources import INDEXES,SENATE_FALLBACK,retrieve_dc
from career_engine.sources import SourceStats
from career_engine.extract import robots_allowed
from career_engine.ats import board_from_url,normalize_posting,FetchError,enrich_regional_metadata
from career_engine.ats_registry import empty_registry,register
from career_engine.jazzhr import jazz_index
from career_engine.regional_selection import select_regional_boards
from career_engine.detail_allocation import detail_plan,description_completeness
from career_engine.publication_audit import build_ledger
from career_engine.collection_health import collection_health
from tools.audit_dc_sources import collection_exit_code
from tools.audit_dc_sources import capture
from tools.expand_ats import expand
from tools.summarize_collection import summarize


class SpecialtyRecoveryTests(unittest.TestCase):
    def test_adapter_fault_preserves_preceding_and_already_yielded_rows(self):
        def adapter(session,source,budget,stats,**kwargs):
            yield {'source':source,'title':'Observed','location':'Washington, DC'}
            if source=='senate':raise ValueError('Do not log arbitrary response state')
        with patch('tools.audit_dc_sources.retrieve_dc',side_effect=adapter):
            data=capture(('senate','unionjobs'),0,DCSession())
        self.assertEqual(len(data['records']),2)
        self.assertEqual(data['stats']['senate']['status'],'partial_failure')
        self.assertEqual(data['stats']['senate']['errors'],['source_adapter_exception_ValueError'])

    def test_senate_missing_route_uses_one_authorized_home_fallback_and_retains_error(self):
        s=DCSession({INDEXES['senate'][0]:DCResponse(status=404),SENATE_FALLBACK:DCResponse(fixture('senate-index.html'))});stats=SourceStats()
        rows=list(retrieve_dc(s,'senate',0,stats,pause=lambda _:None))
        self.assertEqual(len(rows),3);self.assertEqual(stats.status,'partial_failure')
        self.assertEqual([x['status'] for x in stats.index_attempts],['http_404','ok'])
        self.assertEqual(s.calls.count(SENATE_FALLBACK),1)

    def test_senate_denial_never_triggers_fallback(self):
        for status in (403,429):
            s=DCSession({INDEXES['senate'][0]:DCResponse(status=status)})
            list(retrieve_dc(s,'senate',0,pause=lambda _:None));self.assertNotIn(SENATE_FALLBACK,s.calls)

    def test_senate_home_denial_stops_without_other_route_search(self):
        s=DCSession({INDEXES['senate'][0]:DCResponse(status=404),SENATE_FALLBACK:DCResponse(status=403)});stats=SourceStats()
        self.assertFalse(list(retrieve_dc(s,'senate',20,stats,pause=lambda _:None)))
        self.assertEqual(stats.status,'failed');self.assertEqual(len(s.calls),3)

    def test_robots_timeout_disallow_redirect_and_challenge_are_different_fail_closed_evidence(self):
        class Timeout:
            def get(self,*args,**kwargs):raise requests.ReadTimeout()
        url='https://workfornonprofits.org/jobs/district-of-columbia'
        for session,reason in [(Timeout(),'robots_network_ReadTimeout'),
            (DCSession({'https://workfornonprofits.org/robots.txt':DCResponse('User-agent: *\nDisallow: /')}),'robots_explicit_disallow'),
            (DCSession({'https://workfornonprofits.org/robots.txt':DCResponse(status=302)}),'robots_http_302'),
            (DCSession({'https://workfornonprofits.org/robots.txt':DCResponse('<html>Verify you are human</html>')}),'robots_invalid_or_challenge')]:
            evidence=[];self.assertFalse(robots_allowed(session,url,{},evidence));self.assertEqual(evidence[0]['reason'],reason)

    def test_partial_is_explicit_and_only_opt_in_exit_policy_changes(self):
        data={'records':[{'source':'working'}],'stats':{'working':{'status':'ok','unique':1},'blocked':{'status':'failed','errors':['robots_network_ReadTimeout']}}}
        self.assertEqual(collection_exit_code(data),1);self.assertEqual(collection_exit_code(data,True),0)
        self.assertEqual(collection_health(data['stats'])['state'],'partial')
        self.assertEqual(collection_health(data['stats'])['sources']['blocked']['signal'],'red')
        data['records']=[];self.assertEqual(collection_exit_code(data,True),1)

    def test_all_failed_is_always_failure_and_ATS_cannot_hide_original_failure(self):
        self.assertEqual(collection_health({'blocked':{'status':'failed'}})['state'],'failed')
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);base={'records':[],'stats':{'senate':{'status':'failed','errors':['http_404']}}}
            expanded={'records':[{'source':'ats'}],'stats':{**base['stats'],'public_ats':{'status':'ok','unique':1}}}
            (p/'base').write_text(json.dumps(base));(p/'expanded').write_text(json.dumps(expanded))
            self.assertEqual(summarize(p/'base',p/'expanded')['collection_health']['state'],'partial')


class PollingReliabilityTests(unittest.TestCase):
    def test_skewed_registry_has_platform_stratified_exploration_and_bounded_monopoly(self):
        registry=empty_registry()
        for provider,count,root in [('jazzhr',2000,'https://tenant{}.applytojob.com/apply'),('greenhouse',152,'https://job-boards.greenhouse.io/tenant{}'),('lever',9,'https://jobs.lever.co/tenant{}'),('ashby',412,'https://jobs.ashbyhq.com/tenant{}')]:
            for n in range(count):register(registry,root.format(n),{'kind':'source_audit_link' if provider=='jazzhr' else 'observed_URL','location':'Washington, DC','url':'https://source.example/'})
        selected=select_regional_boards(registry);counts=Counter(e['provider'] for e in selected)
        self.assertEqual(len(selected),30);self.assertLessEqual(max(counts.values()),15)
        self.assertEqual(len(counts),4);self.assertEqual(len({e['provider'] for e in selected[:4]}),4)
        explore=[e for e in selected if e['selection_evidence']['route']=='non_targeted_exploration']
        self.assertEqual(len(explore),6);self.assertEqual(len({e['provider'] for e in explore}),4)
        self.assertTrue(any(e['selection_evidence']['strength']>=5 for e in selected))

    def test_exhausted_other_platforms_do_not_manufacture_quotas_or_waste_capacity(self):
        r=empty_registry()
        for n in range(40):register(r,f'https://tenant{n}.applytojob.com/apply',{'kind':'observed_URL'})
        register(r,'https://jobs.lever.co/only-one',{'kind':'observed_URL'})
        selected=select_regional_boards(r,30)
        self.assertEqual(len(selected),30);self.assertEqual(sum(e['provider']=='lever' for e in selected),1)

    def test_per_response_size_failure_does_not_exhaust_global_transport(self):
        url='https://boards-api.greenhouse.io/v1/boards/syntheticcivic/jobs'
        t=transport(Session({url:Response(raw=b'x'*1000)}))
        with self.assertRaisesRegex(FetchError,'response_too_large'):t.get(url,max_bytes=100)
        self.assertEqual(t.global_stop_reason(),'');self.assertTrue(t.requests[0].get('error'))
        t=transport(Session({url:Response(raw=b'x'*1000)}),max_bytes=200)
        with self.assertRaisesRegex(FetchError,'byte_budget'):t.get(url,max_bytes=2000)
        self.assertEqual(t.global_stop_reason(),'byte_budget')

    def run_pipeline(self,max_requests=30):
        registry=empty_registry();a=register(registry,'https://oversized.applytojob.com/apply',{'kind':'observed_URL'});b=register(registry,'https://job-boards.greenhouse.io/syntheticcivic',{'kind':'observed_URL'})
        s=Session({'https://oversized.applytojob.com/robots.txt':Response({},404),
            'https://oversized.applytojob.com/apply':html_response('x'*1_100_000),
            'https://boards-api.greenhouse.io/v1/boards/syntheticcivic/jobs':Response({'jobs':[gh_item()]}),
            'https://boards-api.greenhouse.io/v1/boards/syntheticcivic':Response({'name':'Synthetic Civic'})})
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);(p/'base').write_text(json.dumps({'records':[{'source':'old','url':'https://source.example/','title':'Old','company':'Old'}],'stats':{}}));before=(p/'base').read_bytes();(p/'registry').write_text(json.dumps(registry))
            with patch('tools.expand_ats.select_regional_boards',return_value=list(registry['boards'].values())):
                data=expand(p/'base',p/'registry',p/'out',p/'combined',2,0,transport=transport(s,max_requests=max_requests),extra_details=0,strategy='regional',candidate_capture={'records':[],'stats':{},'discovery':{}})
            self.assertEqual(before,(p/'base').read_bytes());ledger=json.loads((p/'out/coverage-audit.json').read_text())
        return data,ledger

    def test_oversized_board_isolated_and_next_inventory_preserved(self):
        data,_=self.run_pipeline();self.assertEqual(data['discovery']['attempted_boards'],2)
        self.assertEqual(len(data['records']),1);self.assertFalse(data['discovery']['not_attempted_boards'])
        self.assertIn('response_too_large',data['discovery']['boards'][0]['errors'])

    def test_global_limit_logs_selected_but_not_attempted_and_ledger_pending(self):
        data,ledger=self.run_pipeline(1);pending=data['discovery']['not_attempted_boards']
        self.assertEqual(len(pending),1);self.assertEqual(pending[0]['reason'],'request_budget')
        source=next(s for s in ledger['sources'] if s.get('board_key')==pending[0]['board_key'])
        self.assertEqual(source['retrieval_state'],'not_yet_covered');self.assertEqual(source['selection_state'],'selected_not_attempted')


class RegionalDescriptionTests(unittest.TestCase):
    def test_large_employer_does_not_monopolize_details_and_extra_slots_are_regional(self):
        rows=[]
        for board,count in [('large',100),('small1',1),('small2',1),('small3',1)]:
            rows += [{'source':'synthetic','source_id':board+str(i),'identity':board+str(i),'board_key':board,'geography':'dc_dmv','title':'Unusual Role','description':''} for i in range(count)]
        plan=detail_plan(rows,rows,4,9);byid={r['source']+':'+r['source_id']:r for r in rows}
        self.assertEqual(len({byid[x['identity']]['board_key'] for x in plan[:4]}),4)
        self.assertEqual(len(plan),8);self.assertEqual(sum(x['conditional_extra_slot'] for x in plan),4)
        remote=[{**r,'geography':'us_remote'} for r in rows];self.assertEqual(len(detail_plan(remote,remote,4)),4)

    def test_denial_stops_host_but_another_platform_can_supply_description(self):
        gh=board_from_url('https://job-boards.greenhouse.io/syntheticcivic');jazz=board_from_url('https://novelcivic.applytojob.com/apply')
        rows=[normalize_posting(gh,gh_item()),jazz_index(jazz_html(),jazz)[0]]
        s=Session({'https://boards-api.greenhouse.io/v1/boards/syntheticcivic/jobs/1':Response({},429),
            'https://novelcivic.applytojob.com/robots.txt':Response({},404),rows[1]['url']:html_response('<h2>Unusual Role</h2><div id="job-description">Required: communication. Training provided.</div>')})
        log=enrich_regional_metadata(transport(s),rows,{b['key']:b for b in (gh,jazz)},2,adaptive=True)
        self.assertEqual(len(rows),2);self.assertEqual(sum(bool(r['description']) for r in rows),1)
        self.assertEqual({x['status'] for x in log},{'ok','http_429'})
        self.assertEqual(description_completeness(rows)['total']['local_description_fraction'],.5)


if __name__=='__main__':unittest.main()
