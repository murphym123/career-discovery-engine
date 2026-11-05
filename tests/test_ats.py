"""Synthetic safety/coverage tests. No live network; actual excerpts are separate."""
import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch

from career_engine.ats import board_from_url,posting_key,normalize_posting,PublicTransport,FetchError,retrieve_board
from career_engine.ats_registry import empty_registry,register,source_seeds,observed_ats_urls,seed_plan,discover_commoncrawl,select_boards,validate_registry
from career_engine.ats_coverage import assess_increment
from career_engine.review_quarantine import ReviewQuarantine,partition_for_review,digest,employer_title_key
from tools.expand_ats import expand
from tools.publish_experimental import publish

ROOT=Path(__file__).resolve().parents[1]


class Response:
    def __init__(self,data=None,status=200,headers=None,raw=None):
        self.status_code=status;self.headers=headers or {'Content-Type':'application/json'}
        self.body=raw if raw is not None else json.dumps(data).encode();self.closed=False
    def iter_content(self,n):
        for i in range(0,len(self.body),n):yield self.body[i:i+n]
    def close(self):self.closed=True


class Session:
    def __init__(self,mapping=None,callback=None):self.mapping=mapping or {};self.callback=callback;self.calls=[]
    def get(self,url,params=None,**kw):
        self.calls.append((url,params,kw))
        if self.callback:return self.callback(url,params or {})
        value=self.mapping.get(url,Response({},404))
        return value() if callable(value) else value


def transport(session,**kw):return PublicTransport(session,max_requests=kw.pop('max_requests',100),pause=lambda _:None,**kw)


def gh_item(n=1,location='Washington, DC',**kw):
    return {'id':n,'title':'Unfamiliar Professional Opportunity','internal_job_id':n,'location':{'name':location},
            'absolute_url':f'https://job-boards.greenhouse.io/syntheticcivic/jobs/{n}',**kw}


class URLAndTransportSafetyTests(unittest.TestCase):
    def test_tenants_come_from_observed_URLs_not_employer_names(self):
        for url,p,t in [('https://boards.greenhouse.io/syntheticcivic/jobs/1','greenhouse','syntheticcivic'),
                        ('https://jobs.lever.co/new-council/00000000-0000-0000-0000-000000000000','lever','new-council'),
                        ('https://jobs.eu.lever.co/new-council','lever_eu','new-council'),
                        ('https://jobs.ashbyhq.com/Novel%20Institute/12345678','ashby','Novel Institute')]:
            b=board_from_url(url);self.assertEqual((b['provider'],b['token']),(p,t))
        self.assertIsNone(board_from_url('A desirable employer'))
        self.assertFalse(empty_registry()['boards'])

    def test_untrusted_urls_cannot_escape_provider_endpoints(self):
        bad=['https://jobs.lever.co.evil.example/new','https://user:secret@jobs.lever.co/new',
             'https://jobs.lever.co:8443/new','https://jobs.ashbyhq.com/%2e%2e',
             'https://jobs.ashbyhq.com/foo%2fadmin','https://localhost/new','file:///new',
             'https://api.ashbyhq.com/non-user-graphql','https://job-boards.greenhouse.io/robots.txt']
        self.assertTrue(all(board_from_url(u) is None for u in bad))
        for u in ['https://boards-api.greenhouse.io/v1/boards/../jobs','https://api.ashbyhq.com/api/private',
                  'https://api.lever.co/v0/postings/foo/apply','http://api.lever.co/v0/postings/foo',
                  'https://index.commoncrawl.org/../private','https://example.org/jobs']:
            self.assertFalse(PublicTransport.allowed(u),u)

    def test_cross_host_and_downgrade_redirects_never_followed(self):
        url='https://api.ashbyhq.com/posting-api/job-board/synthetic'
        for target in ['http://api.ashbyhq.com/posting-api/job-board/synthetic','https://example.org/private','https://api.ashbyhq.com/private']:
            s=Session({url:Response({},302,{'Location':target})})
            with self.assertRaises(FetchError):transport(s).json(url)
            self.assertEqual(len(s.calls),1)

    def test_rate_denial_stops_host_without_retry(self):
        url='https://api.lever.co/v0/postings/synthetic';s=Session({url:Response({},429,{'Retry-After':'3600'})});t=transport(s)
        with self.assertRaises(FetchError):t.json(url)
        with self.assertRaises(FetchError):t.json(url)
        self.assertEqual(len(s.calls),1);self.assertEqual(t.requests[0]['retry_after'],'3600')

    def test_request_byte_mime_time_limits_and_close(self):
        url='https://api.ashbyhq.com/posting-api/job-board/synthetic'
        r=Response(raw=b'x'*100);s=Session({url:r})
        with self.assertRaises(FetchError):transport(s).get(url,max_bytes=10)
        self.assertTrue(r.closed)
        t=transport(Session(),max_requests=0)
        with self.assertRaises(FetchError):t.json(url)
        t=transport(Session(),max_seconds=-1)
        with self.assertRaises(FetchError):t.json(url)
        s=Session({url:Response(raw=b'<html>sign in</html>',headers={'Content-Type':'text/html'})})
        with self.assertRaises(FetchError):transport(s).json(url)

    def test_optional_public_page_respects_robots(self):
        root='https://jobs.ashbyhq.com/synthetic'
        from career_engine.ats import board_name
        s=Session({'https://jobs.ashbyhq.com/robots.txt':Response(raw=b'User-agent: *\nDisallow: /',headers={'Content-Type':'text/plain'})})
        self.assertEqual(board_name(transport(s),board_from_url(root)),'')
        self.assertEqual(len(s.calls),1)


class ProviderNormalizationTests(unittest.TestCase):
    def test_greenhouse_metadata_first_preserves_every_region_and_unusual_title(self):
        b=board_from_url('https://job-boards.greenhouse.io/syntheticcivic');root='https://boards-api.greenhouse.io/v1/boards/syntheticcivic'
        s=Session({root:Response({'name':'Synthetic Civic Organization'}),root+'/jobs':Response({'jobs':[gh_item(),gh_item(2,'London, UK')]}),
                   root+'/jobs/1':Response(gh_item(content='<p>Research and event support. Two years experience.</p>',company_name='Synthetic Civic Organization',first_published='2026-11-05T00:00:00Z'))})
        rows,stats=retrieve_board(transport(s),b)
        self.assertEqual(len(rows),2);self.assertTrue(rows[0]['description']);self.assertFalse(rows[1]['description'])
        self.assertEqual(rows[0]['geography'],'dc_dmv');self.assertEqual(rows[1]['geography'],'elsewhere')
        self.assertEqual(rows[0]['posted_at'],'2026-11-05T00:00:00Z')
        self.assertEqual(stats['received'],2);self.assertEqual(s.calls[0][0],root+'/jobs')
        self.assertFalse(any((call[1] or {}).get('content') for call in s.calls))

    def test_description_denial_does_not_erase_metadata(self):
        b=board_from_url('https://job-boards.greenhouse.io/syntheticcivic');root='https://boards-api.greenhouse.io/v1/boards/syntheticcivic'
        s=Session({root:Response({'name':'Synthetic Civic'}),root+'/jobs':Response({'jobs':[gh_item(),gh_item(2)]}),root+'/jobs/1':Response({},403)})
        rows,stats=retrieve_board(transport(s),b)
        self.assertEqual(len(rows),2);self.assertEqual(stats['status'],'partial_failure')
        self.assertFalse(any(c[0].endswith('/jobs/2') for c in s.calls))

    def test_optional_name_failure_does_not_erase_jobs(self):
        b=board_from_url('https://job-boards.greenhouse.io/syntheticcivic');root='https://boards-api.greenhouse.io/v1/boards/syntheticcivic'
        rows,stats=retrieve_board(transport(Session({root:Response({},500),root+'/jobs':Response({'jobs':[gh_item()]})})),b,detail_budget=0)
        self.assertEqual(len(rows),1);self.assertTrue(stats['warnings'])
        self.assertEqual(rows[0]['company_evidence'],'board_identifier_only_not_legal_employer_verified')

    def test_provider_and_tenant_scope_identities_and_reject_mismatched_links(self):
        b=board_from_url('https://job-boards.greenhouse.io/syntheticcivic')
        a=normalize_posting(b,gh_item());other=normalize_posting(board_from_url('https://job-boards.greenhouse.io/other'),
              {**gh_item(),'absolute_url':'https://job-boards.greenhouse.io/other/jobs/1'})
        self.assertNotEqual(a['identity'],other['identity'])
        with self.assertRaises(ValueError):normalize_posting(b,{**gh_item(),'absolute_url':'https://job-boards.greenhouse.io/other/jobs/1'})
        self.assertEqual(posting_key('https://boards.greenhouse.io/syntheticcivic/jobs/1?gh_src=tracker'),a['identity'])

    def test_lever_pagination_and_partial_failure(self):
        b=board_from_url('https://jobs.lever.co/syntheticcivic')
        def cb(url,params):
            if 'robots.txt' in url:return Response(raw=b'User-agent: *\nDisallow: /',headers={'Content-Type':'text/plain'})
            if params.get('skip')==100:return Response({},500)
            return Response([{'id':f'00000000-0000-0000-0000-{n:012d}','text':'Program Operations',
                              'hostedUrl':f"https://jobs.lever.co/syntheticcivic/00000000-0000-0000-0000-{n:012d}",
                              'categories':{'location':'Washington, DC','allLocations':['Washington, DC','London']},
                              'descriptionPlain':'Support educational programs','lists':[{'text':'Requirements','content':'Writing and research.'}]} for n in range(100)])
        rows,stats=retrieve_board(transport(Session(callback=cb)),b)
        self.assertEqual(len(rows),100);self.assertEqual(stats['status'],'partial_failure')
        self.assertIn('Writing and research',rows[0]['description']);self.assertEqual(rows[0]['geography'],'dc_dmv')

    def test_ashby_secondary_location_hybrid_and_unlisted_policy(self):
        b=board_from_url('https://jobs.ashbyhq.com/syntheticcivic')
        item={'title':'Unexpected Professional Role','location':'New York','secondaryLocations':[{'location':'Washington, DC'}],
              'isRemote':True,'workplaceType':'Hybrid','isListed':True,'publishedAt':'2026-11-01',
              'descriptionPlain':'Original description','jobUrl':b['url']+'/00000000-0000-0000-0000-000000000001'}
        row=normalize_posting(b,item);self.assertEqual(row['geography'],'dc_dmv');self.assertNotIn('Remote;',row['location'])
        unlisted={**item,'isListed':False}
        s=Session({'https://api.ashbyhq.com/posting-api/job-board/syntheticcivic':Response({'jobs':[item,unlisted]})})
        rows,stats=retrieve_board(transport(s),b);self.assertEqual(len(rows),1);self.assertEqual(stats['unlisted_omitted'],1)

    def test_missing_geography_and_description_stay_unknown(self):
        b=board_from_url('https://jobs.ashbyhq.com/syntheticcivic')
        row=normalize_posting(b,{'title':'Special Opportunity','location':'','jobUrl':b['url']+'/00000000-0000-0000-0000-000000000001'})
        self.assertEqual(row['geography'],'unknown');self.assertEqual(row['detail_status'],'index_only')
        row=normalize_posting(b,{'title':'Hybrid Role','location':'Maryland','isRemote':True,'workplaceType':'Hybrid','jobUrl':b['url']+'/00000000-0000-0000-0000-000000000002'})
        self.assertEqual(row['geography'],'regional_worksite_unknown')


class EmployerDiscoveryTests(unittest.TestCase):
    def test_observed_links_from_html_plaintext_and_encoded_json(self):
        text='<a href="https://jobs.lever.co/unknown-council/12345678?x=1&amp;y=2">apply</a> https:\\/\\/jobs.ashbyhq.com/unknown-foundation <a href="https://example.org/unrelated">x</a>'
        urls=observed_ats_urls(text)
        self.assertEqual(len(urls),2);self.assertTrue(any('unknown-council' in x for x in urls))
        r=empty_registry();source_seeds(r,[{'url':'https://board.example/job/1','source':'source','source_id':'1','description':text}])
        self.assertEqual(len(r['boards']),2);self.assertEqual(r['boards']['lever:unknown-council']['evidence'][0]['kind'],'source_audit_link')

    def test_free_index_discovers_arbitrary_unconfigured_employer_and_tracks_provenance(self):
        def cb(url,params):
            if url.endswith('collinfo.json'):return Response([{'id':'CC-MAIN-2026-43','cdx-api':'https://malicious.example/ignored'}])
            if params.get('showNumPages'):return Response({'pages':20})
            return Response(raw=b'{"url":"https://job-boards.greenhouse.io/neverconfiguredcivic/jobs/9","timestamp":"20261001010101","status":"200"}\n',headers={'Content-Type':'text/plain'})
        r=empty_registry();logs=discover_commoncrawl(transport(Session(callback=cb)),r,1)
        self.assertIn('greenhouse:neverconfiguredcivic',r['boards'])
        self.assertEqual(logs[0]['new_boards'],1);self.assertNotEqual(logs[0]['page'],None)
        evidence=r['boards']['greenhouse:neverconfiguredcivic']['evidence'][0]
        self.assertEqual(evidence['kind'],'commoncrawl_url_index');self.assertEqual(evidence['archive_timestamp'],'20261001010101')
        self.assertEqual(r['query_offset'],1)

    def test_unavailable_primary_index_can_use_public_alternate_not_on_denial(self):
        def cb(url,params):
            if url.endswith('collinfo.json'):return Response([{'id':'CC-MAIN-2026-43'}])
            if 'commoncrawl' in url:return Response({},504)
            if params.get('showNumPages'):return Response({'pages':1})
            return Response([['original','timestamp'],['https://job-boards.greenhouse.io/novelpublicorg','20261001']])
        r=empty_registry();s=Session(callback=cb);logs=discover_commoncrawl(transport(s),r,1)
        self.assertIn('greenhouse:novelpublicorg',r['boards']);self.assertEqual(logs[0]['fallback']['status'],'ok')
        def denied(url,params):return Response([{'id':'CC-MAIN-2026-43'}]) if url.endswith('collinfo.json') else Response({},403)
        s=Session(callback=denied);r=empty_registry();discover_commoncrawl(transport(s),r,3)
        self.assertFalse(any('web.archive.org' in x[0] for x in s.calls))

    def test_query_rotation_is_sector_diverse_with_unrestricted_exploration(self):
        plan=seed_plan(0,120)
        self.assertEqual({p['provider'] for p in plan},{'greenhouse','lever','ashby'})
        self.assertTrue(any(p['family']=='unrestricted' for p in plan))
        for term in ['research','consult','association','policy','foundation','university']:
            self.assertTrue(any(term in p['pattern'] for p in plan))
        self.assertNotEqual(seed_plan(0,6),seed_plan(6,6))

    def test_registry_rotation_new_boards_primary_known_refresh_secondary_and_failure_cooldown(self):
        r=empty_registry()
        for n in range(1000):register(r,f'https://jobs.ashbyhq.com/synthetic-{n}',{'kind':'index','url':'https://index.example'})
        self.assertEqual(len(select_boards(r,30)),30)
        for n,e in enumerate(r['boards'].values()):
            if n<300:e.update(last_checked_run=0,status='ok',dc_dmv=1)
        chosen=select_boards(r,30)
        self.assertGreaterEqual(sum(e['last_checked_run'] is None for e in chosen),21)
        bad=next(iter(r['boards'].values()));bad.update(last_checked_run=r['run'],status='failed')
        self.assertNotIn(bad['key'],{e['key'] for e in select_boards(r,30)})
        self.assertEqual(len(chosen),len({e['key'] for e in chosen}))

    def test_archive_missing_page_count_advances_instead_of_repeating_page_zero(self):
        from career_engine.ats_registry import archive_fallback
        def cb(url,params):
            if params.get('showNumPages'):return Response([['original','timestamp']])
            return Response([['original','timestamp'],['https://jobs.lever.co/novel-public-board','20261001']])
        r=empty_registry();s=Session(callback=cb);t=transport(s);q=seed_plan(1,1)[0]
        first=archive_fallback(t,r,q,0);second=archive_fallback(t,r,q,0)
        self.assertFalse(first['page_count_known']);self.assertEqual((first['page'],second['page']),(0,1))
        self.assertEqual([c[1]['page'] for c in s.calls if not c[1].get('showNumPages')],[0,1])

    def test_registry_does_not_trust_modified_host_or_credential_tenant(self):
        r=empty_registry();register(r,'https://jobs.lever.co/novel',{'kind':'source','url':'https://source.example'})
        bad=copy.deepcopy(r);bad['boards']['lever:novel']['url']='https://attacker.example/jobs'
        self.assertFalse(validate_registry(bad)['boards'])
        with self.assertRaises(ValueError):retrieve_board(transport(Session()),{'key':'lever:novel','provider':'lever','token':'../private','url':'https://jobs.lever.co/novel'})


class MarginalCoverageAndIntegrationTests(unittest.TestCase):
    def test_exact_duplicate_vs_possible_crosspost_and_distinct_requisitions(self):
        b=board_from_url('https://job-boards.greenhouse.io/syntheticcivic')
        rows=[normalize_posting(b,gh_item(n),company='Civic Organization') for n in (1,2,3)]
        base=[{'url':rows[0]['url'],'company':'Civic Organization','title':rows[0]['title'],
               'employer_board_urls':[rows[0]['url']]}]
        metrics=assess_increment(base,rows)
        self.assertEqual(rows[0]['duplicate_status'],'exact_existing_source')
        self.assertEqual(rows[1]['duplicate_status'],'possible_crosspost_review')
        self.assertEqual(len(rows),3);self.assertEqual(metrics['dc_not_matched_to_five_sources'],0)
        self.assertEqual(rows[1]['possible_same_employer_title_group']['count'],3)

    def test_related_outbound_posting_link_is_not_proof_of_same_vacancy(self):
        b=board_from_url('https://job-boards.greenhouse.io/syntheticcivic')
        row=normalize_posting(b,gh_item(),company='Civic Organization')
        base=[{'url':'https://specialty.example/old','title':'Different Advertised Role','company':'Civic Organization','employer_board_urls':[row['url']]}]
        assess_increment(base,[row])
        self.assertEqual(row['duplicate_status'],'possible_crosspost_review')
        self.assertEqual(row['duplicate_evidence']['source_urls'],[base[0]['url']])

    def test_offmarket_technical_and_unknown_are_retained_not_counted_as_DC_gain(self):
        b=board_from_url('https://job-boards.greenhouse.io/syntheticcivic')
        rows=[normalize_posting(b,gh_item(1,'London')),normalize_posting(b,gh_item(2,'')),normalize_posting(b,{**gh_item(3),'title':'Senior Engineer'})]
        m=assess_increment([],rows)
        self.assertEqual(len(rows),3);self.assertEqual(m['dc_dmv_labels'],1);self.assertEqual(m['outside_region'],1)
        self.assertEqual(m['dc_new_title_specialist_signals'],1);self.assertEqual(m['geography_review'],1)

    def test_pipeline_preserves_five_source_bytes_and_all_rows_without_paid_services(self):
        b=board_from_url('https://job-boards.greenhouse.io/syntheticcivic');api='https://boards-api.greenhouse.io/v1/boards/syntheticcivic'
        s=Session({api:Response({'name':'Synthetic Civic'}),api+'/jobs':Response({'jobs':[gh_item()]}),api+'/jobs/1':Response(gh_item(content='Original public research and events requirements.'))})
        base={'records':[{'source':'senate','source_id':'9','title':'Source Role','company':'Source Employer','url':'https://source.example/job','location':'Washington DC','description':''}], 'stats':{'senate':{'status':'ok','errors':[]}}}
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);src=p/'original.json';src.write_text(json.dumps(base));original=src.read_bytes()
            data=expand(src,p/'registry.json',p/'ats',p/'combined.json',1,0,[{'url':b['url'],'evidence_url':'https://public.example/observed'}],transport(s))
            self.assertEqual(src.read_bytes(),original)
            combined=json.loads((p/'combined.json').read_text());self.assertEqual(len(combined['records']),2)
            self.assertEqual(data['coverage']['dc_not_matched_to_five_sources'],1)
            self.assertTrue((p/'ats/ats-source-audit.csv').exists());self.assertTrue((p/'registry.json').exists())
            self.assertTrue(all(call[0].startswith('https://boards-api.greenhouse.io/') for call in s.calls))

    def test_quarantined_records_are_retained_but_never_passed_to_ranker(self):
        row={'title':'Unscored Role','company':'New Org','source':'ats_greenhouse','source_id':'x:1',
             'url':'https://job-boards.greenhouse.io/x/jobs/1','description':'Secret held-out requirements','location':'Washington DC'}
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);folder=p/'review'/'future';folder.mkdir(parents=True)
            (folder/'seal.json').write_text(json.dumps({'status':'sealed_awaiting_independent_review','records':[{'url_sha256':digest(row['url'])}]}))
            q=ReviewQuarantine(p/'review');available,withheld=partition_for_review([row],q)
            self.assertFalse(available);self.assertEqual(len(withheld),1)
            src=p/'audit.json';src.write_text(json.dumps({'records':[row],'stats':{}}))
            from career_engine.triage import triage
            def guarded(records,*a,**kw):
                self.assertFalse(records);return triage(records,*a,**kw)
            with patch('tools.publish_experimental.partition_for_review',return_value=(available,withheld)),patch('tools.publish_experimental.engine.triage',side_effect=guarded):
                status=publish(src,p/'rank',as_of=datetime(2026,11,5,tzinfo=timezone.utc))
            result=json.loads((p/'rank/experimental-ranked-brief.json').read_text())
            self.assertEqual(status['counts']['retained'],1);self.assertEqual(result['audit'][0]['decision'],'withheld_for_independent_review')
            self.assertEqual(result['independent_review_withheld'],1);self.assertFalse(result['shortlist'])




class AdditionalInspectionAndWorkflowTests(unittest.TestCase):
    def test_shared_inspection_keeps_atypical_rows_and_does_not_retry_failed_details(self):
        from career_engine.ats import enrich_regional_metadata
        b=board_from_url('https://job-boards.greenhouse.io/syntheticcivic')
        rows=[normalize_posting(b,gh_item(n,title=('Program Assistant' if n%2 else 'Unusual Professional Role'))) for n in range(1,21)]
        rows[0]['detail_status']='http_404'
        def cb(url,params):
            return Response(gh_item(int(url.rsplit('/',1)[-1]),content='Public requirements and responsibilities.'))
        s=Session(callback=cb)
        before={r['identity'] for r in rows}
        log=enrich_regional_metadata(transport(s),rows,{b['key']:b},10,9)
        self.assertEqual(len(log),10);self.assertEqual(len(s.calls),10)
        self.assertFalse(any(c[0].endswith('/jobs/1') for c in s.calls))
        self.assertIn('low_signal_random_review',{x['stratum'] for x in log})
        self.assertEqual(before,{r['identity'] for r in rows});self.assertEqual(len(rows),20)
        self.assertTrue(all(r['company_evidence']=='board_identifier_only_not_legal_employer_verified' for r in rows))

    def test_detail_response_cannot_replace_a_different_posting(self):
        from career_engine.ats import enrich_regional_metadata
        b=board_from_url('https://job-boards.greenhouse.io/syntheticcivic');row=normalize_posting(b,gh_item())
        s=Session(callback=lambda u,p:Response(gh_item(999,content='Wrong listing.')))
        log=enrich_regional_metadata(transport(s),[row],{b['key']:b},1)
        self.assertEqual(row['identity'],b['key']+':1');self.assertFalse(row['description'])
        self.assertIn('mismatch',log[0]['status'])

    def test_denial_cooldown_survives_next_manual_run(self):
        b=board_from_url('https://job-boards.greenhouse.io/syntheticcivic')
        def cb(url,params):return Response({},429,{'Retry-After':'3600'})
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);src=p/'base.json';src.write_text(json.dumps({'records':[],'stats':{}}))
            s=Session(callback=cb)
            expand(src,p/'registry.json',p/'out',p/'combined.json',1,0,[{'url':b['url'],'evidence_url':'https://public.example/evidence'}],transport(s))
            saved=json.loads((p/'registry.json').read_text())
            self.assertIn('boards-api.greenhouse.io',saved['host_not_before']);self.assertEqual(len(s.calls),1)
            # Force registry eligibility to verify the wall-clock guard itself.
            saved['boards'][b['key']]['last_checked_run']=None
            (p/'registry.json').write_text(json.dumps(saved));s=Session(callback=cb)
            expand(src,p/'registry.json',p/'out',p/'combined.json',1,0,transport=transport(s))
            self.assertFalse(s.calls)

    def test_workflow_preserves_inputs_artifacts_and_free_expansion_gate(self):
        import re,subprocess
        from tests.test_issue9_workflow import script
        text=(ROOT/'.github/workflows/discovery.yml').read_text()
        inputs=text.split('    inputs:',1)[1].split('\npermissions:',1)[0]
        self.assertEqual(len(re.findall(r'^      [a-z_]+:',inputs,re.M)),10)
        toggle=inputs.split('      expanded_ats:',1)[1].split('      ats_board_budget:',1)[0]
        self.assertIn('default: false',toggle)
        step=text.split('      - name: Expand public ATS',1)[1].split('      - name:',1)[0]
        self.assertIn("inputs.source_mode == 'dc'",step);self.assertIn('inputs.expanded_ats',step)
        self.assertNotIn('BRAVE',step);self.assertNotIn('allow-paid',step)
        self.assertIn('public-ats-registry',text);self.assertIn('retention-days: 90',text)
        code=script('Expand public ATS discovery without replacing the five-source audit')
        subprocess.run(['/bin/bash','-n'],input=code,text=True,check=True)
        for ext in ('json','csv','md'):
            self.assertIn('output/dc-source-audit.'+ext,text);self.assertIn('output/dc-expanded-audit.'+ext,text)

    def test_actual_rank_step_chooses_combined_only_when_requested_and_available(self):
        import os,subprocess
        from tests.test_issue9_workflow import script
        code=script('Publish experimental ranking from the existing DC audit')
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);(p/'bin').mkdir();(p/'output').mkdir()
            stub=p/'bin/python';stub.write_text('#!/bin/bash\nprintf "%s\\n" "$*" > command.log\nmkdir -p output/experimental-ranking\necho EXPERIMENTAL > output/experimental-ranking/README.md\n');stub.chmod(0o755)
            for requested,exists,expected in [('false',True,'dc-source-audit.json'),('true',True,'dc-expanded-audit.json'),('true',False,'dc-source-audit.json')]:
                combined=p/'output/dc-expanded-audit.json'
                if exists:combined.write_text('{}')
                elif combined.exists():combined.unlink()
                env={**os.environ,'PATH':str(p/'bin')+os.pathsep+os.environ['PATH'],'INSPECTION_SEED':'9','EXPANDED_ATS':requested,'GITHUB_STEP_SUMMARY':str(p/'summary')}
                subprocess.run(['/bin/bash','-c',code],cwd=p,env=env,check=True,capture_output=True)
                self.assertIn('--input output/'+expected,(p/'command.log').read_text())


class ActualMinimizedRecordTests(unittest.TestCase):
    def test_three_providers_normalize_actual_response_metadata_offline(self):
        folder=ROOT/'tests/fixtures/public_ats_actual'
        for entry in json.loads((folder/'manifest.json').read_text()):
            path=folder/entry['fixture']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),entry['fixture_sha256'])
            fixture=json.loads(path.read_text())
            with patch('requests.sessions.Session.request',side_effect=AssertionError('Fixtures must be offline')):
                row=normalize_posting(fixture['board'],fixture['posting'],fixture['company'])
            for field,value in fixture['expected'].items():self.assertEqual(row[field],value)
            self.assertFalse(ReviewQuarantine().matches(row))
            self.assertIn('ACTUAL RETRIEVED',fixture['kind'])
            self.assertEqual(len(row['description'].split()),20)


if __name__=='__main__':unittest.main()
