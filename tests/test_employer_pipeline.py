"""Synthetic employer-frame tests. No holdout or live network."""
import copy
import csv
import hashlib
import json
from datetime import datetime,timezone,timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from career_engine.ats import PublicTransport,board_from_url,FetchError
from career_engine.ats_registry import empty_registry,register
from career_engine.employer_frame import validate_rows,load_frame,select_batch,acquire_cbe,FrameTransport,CBE_LAYER,CBE_FIELDS
from career_engine.employer_pipeline import PipelineTransport,extract_channels,page_identity,channel_url,run_pipeline,resolve_employer
from career_engine.registry_state import save_registry
from tools.discover_employers import run
from tests.test_ats import Session,Response,gh_item,transport

SITE='https://civic.example/'
BOARD='https://job-boards.greenhouse.io/syntheticcivic'
API='https://boards-api.greenhouse.io/v1/boards/syntheticcivic'


def frame_row(i=1,**kw):
    return {'frame_id':f'external:{i}','legal_name':'Synthetic Civic LLC','website_candidate':SITE,
            'source_url':'https://directory.example/organizations','source_sha256':'a'*64,'source_observed_at':'2026-11-06T20:00:00+00:00',
            'eligibility':'eligible','footprint':'DC headquarters claim, not job worksite',**kw}


def html(text):return Response(raw=text.encode(),headers={'Content-Type':'text/html'})


def discovery(mapping=None,**kw):
    return PipelineTransport(Session(mapping),pause=lambda _:None,
        resolver=lambda *a,**k:[(None,None,None,None,('8.8.8.8',443))],**kw)


def site_mapping(home=None):
    return {'https://civic.example/robots.txt':Response(raw=b'User-agent: *\nAllow: /',headers={'Content-Type':'text/plain'}),
            SITE:html(home or '<title>Synthetic Civic</title><a href="/careers">Careers</a>'),
            'https://civic.example/careers':html('<h1>Synthetic Civic Careers</h1><a href="'+BOARD+'">Open jobs</a>')}


def collection():
    return transport(Session({API:Response({'name':'Synthetic Civic'}),API+'/jobs':Response({'jobs':[gh_item(content='<p>Original public duties. Bachelor degree. Zero to two years.</p>'),gh_item(2,'London, UK',content='Specialist overseas duties')],'meta':{'total':2}})}))


class FrameTests(unittest.TestCase):
    def test_csv_json_provenance_and_nonpersonal_schema(self):
        row=frame_row(contact_name='Private person',email='private@example.org')
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);(p/'frame.json').write_text(json.dumps({'rows':[row]}))
            with (p/'frame.csv').open('w',newline='') as f:
                w=csv.DictWriter(f,fieldnames=list(row));w.writeheader();w.writerow(row)
            for file in (p/'frame.csv',p/'frame.json'):
                loaded=load_frame(file);self.assertEqual(loaded['rows'][0]['legal_name'],row['legal_name'])
                self.assertNotIn('Private person',json.dumps(loaded));self.assertNotIn('private@example.org',json.dumps(loaded))
                self.assertEqual(loaded['input_sha256'],hashlib.sha256(file.read_bytes()).hexdigest())

    def test_contact_address_in_website_field_is_not_imported_or_used_for_selection(self):
        rows=validate_rows([frame_row(website_candidate='https://private.person@example.org')])
        self.assertEqual(rows[0]['website_candidate'],'');self.assertEqual(rows[0]['frame_id'],'external:1')
        self.assertNotIn('private.person',json.dumps(rows))
        self.assertEqual(select_batch({'rows':rows},1)['rows'][0]['frame_id'],'external:1')

    def test_missing_bad_provenance_duplicate_entities_and_oversize_rejected(self):
        for rows in [[],[{'legal_name':'Untraceable'}],[frame_row(),frame_row()],[frame_row(source_sha256='bad')],[frame_row(eligibility='hiring')],[frame_row(source_url='http://localhost/private')]]:
            with self.assertRaises(ValueError):validate_rows(rows)
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'frame.json';p.write_bytes(b'x'*4_000_001)
            with self.assertRaises(ValueError):load_frame(p)

    def test_selection_is_frozen_order_independent_and_not_platform_or_site_based(self):
        rows=[frame_row(i,website_candidate='' if i%2 else SITE) for i in range(45)]+[frame_row(50,eligibility='unknown'),frame_row(51,eligibility='ineligible')]
        a=select_batch({'rows':rows},30,'seed');b=select_batch({'rows':list(reversed(rows))},30,'seed')
        self.assertEqual(a['rows'],b['rows']);self.assertEqual(len(a['rows']),30)
        self.assertTrue(any(not r['website_candidate'] for r in a['rows']));self.assertEqual(a['selection']['unknown_eligibility'],1)
        with self.assertRaises(ValueError):select_batch({'rows':rows},41)

    def test_cbe_pagination_dates_no_contacts_and_complete_frame(self):
        now=datetime(2026,11,6,tzinfo=timezone.utc);millis=now.timestamp()*1000
        def callback(url,p):
            self.assertEqual(p['outFields'],CBE_FIELDS);self.assertEqual(p['returnGeometry'],'false')
            self.assertNotIn('CONTACTNAME',p['outFields']);self.assertNotIn('EMAIL',p['outFields'])
            i=1 if p['resultOffset']==0 else 2
            return Response({'features':[{'attributes':{'OBJECTID':i,'BUSINESSNAME':f'Firm {i}','WEBSITE':'firm.example','STARTDATE':millis-100,'EXPIRATIONDATE':millis+100 if i==1 else millis-1}}],
                             'exceededTransferLimit':i==1})
        t=FrameTransport(Session(callback=callback),pause=lambda _:None)
        data=acquire_cbe(t,now);self.assertEqual(len(t.requests),2)
        self.assertEqual([r['eligibility'] for r in data['rows']],['eligible','ineligible']);self.assertEqual(len(data['source_requests']),2)
        self.assertEqual(data['rows'][0]['website_candidate'],'https://firm.example')
        with self.assertRaises(FetchError):acquire_cbe(FrameTransport(Session(callback=callback),pause=lambda _:None),now,max_pages=1)
        self.assertFalse(t.allowed('https://directory.example/private'))


class ChannelResolutionTests(unittest.TestCase):
    def test_exact_observed_channels_queries_and_email_privacy(self):
        text='''<a href="https://job-boards.greenhouse.io/syntheticcivic">Jobs</a>
        <iframe src="https://careers.synthetic.myworkdayjobs.com/en-US/jobs"></iframe>
        <a href="https://www.idealist.org/en/nonprofit-jobs/123">Careers on Idealist</a>
        <a href="/opportunities.pdf">Job openings</a><a href="/careers?category=all">Careers</a>
        <a href="mailto:jobs@civic.example">Apply for jobs</a>
        <a href="mailto:private.person@civic.example">Apply for employment</a>
        <a href="https://vendor.example/jobs?token=secret">Careers</a>'''
        leads=extract_channels(text,SITE,SITE);kinds={r['kind'] for r in leads}
        self.assertEqual(kinds,{'supported_ats','unsupported_ats','secondary_board','employer_pdf','employer_html','email_application'})
        self.assertIn('https://civic.example/careers?category=all',{r['url'] for r in leads})
        self.assertIn('mailto:jobs@civic.example',{r['url'] for r in leads});self.assertNotIn('private.person',json.dumps(leads));self.assertNotIn('secret',json.dumps(leads))

    def test_identity_corroboration_and_affiliate_leads_are_not_legal_entity_proof(self):
        identity=page_identity('<title>Synthetic Civic</title><p>Part of Another Parent Corporation</p>','Synthetic Civic LLC')
        self.assertEqual(identity['status'],'name_corroborated');self.assertEqual(identity['parent_affiliate_status'],'not_established')
        self.assertTrue(identity['relationship_leads']);self.assertEqual(page_identity('<title>Unrelated domain for sale</title>','Synthetic Civic LLC')['status'],'identity_unresolved')

    def test_no_guessed_career_paths_or_tenants_and_no_zero_vacancy_claim(self):
        d=discovery(site_mapping('<title>Synthetic Civic</title><p>We help customers. No careers links.</p>'))
        result=resolve_employer(frame_row(),d)
        self.assertFalse(result['channels']);self.assertEqual(len(d.session.calls),2)
        self.assertEqual(result['vacancy_state'],'unknown_not_zero')
        self.assertIn('no_hiring_channel_observed',{r['reason'] for r in result['review_queue']})

    def test_robots_denial_and_challenges_are_not_bypassed(self):
        m=site_mapping();m['https://civic.example/robots.txt']=Response(raw=b'User-agent: *\nDisallow: /',headers={'Content-Type':'text/plain'})
        d=discovery(m);result=resolve_employer(frame_row(),d)
        self.assertEqual(len(d.session.calls),1);self.assertIn('robots',result['review_queue'][0]['reason'])
        m=site_mapping('<title>Just a moment</title><p>Verify access</p>');result=resolve_employer(frame_row(),discovery(m))
        self.assertIn('access_challenge_not_bypassed',{r['reason'] for r in result['review_queue']})

    def test_private_urls_dns_and_redirects_are_rejected(self):
        for url in ['https://127.0.0.1/careers','https://user:secret@example.org/jobs','https://example.org/wp-admin/jobs','https://example.org/jobs?auth=secret']:
            self.assertFalse(channel_url(url))
        d=PipelineTransport(Session(),pause=lambda _:None,resolver=lambda *a,**k:[(None,None,None,None,('127.0.0.1',443))])
        self.assertFalse(d.observe(SITE));self.assertFalse(d.session.calls)
        d=discovery(site_mapping());self.assertFalse(d.redirect_allowed('civic.example','https://other.example/careers'))

    def test_cross_entity_redirect_is_preserved_as_untrusted_evidence_without_following(self):
        m=site_mapping();m[SITE]=Response({},302,{'Location':'https://new-parent.example/careers'})
        d=discovery(m);e=resolve_employer(frame_row(),d)
        self.assertEqual(e['channels'][0]['kind'],'unresolved_redirect')
        self.assertEqual(e['channels'][0]['url'],'https://new-parent.example/careers')
        self.assertFalse(e['channels'][0]['identity_verified'])
        self.assertFalse(any(c[0].startswith('https://new-parent.example') for c in d.session.calls))

    def test_redirect_target_robots_and_actual_page_base(self):
        m=site_mapping();m[SITE]=Response({},302,{'Location':'https://civic.example/en/'})
        m['https://civic.example/en/']=html('<title>Synthetic Civic</title><a href="careers">Careers</a>')
        m['https://civic.example/en/careers']=html('<h1>Synthetic Civic</h1><a href="'+BOARD+'">Jobs</a>')
        d=discovery(m);r=resolve_employer(frame_row(),d)
        self.assertIn('https://civic.example/en/careers',[p['url'] for p in r['pages']])
        self.assertEqual(r['pages'][0]['final_url'],'https://civic.example/en/')
        m['https://civic.example/robots.txt']=Response(raw=b'User-agent: *\nDisallow: /en/',headers={'Content-Type':'text/plain'})
        d=discovery(m);resolve_employer(frame_row(),d)
        self.assertFalse(any(c[0]=='https://civic.example/en/' for c in d.session.calls))

    def test_bounded_prefix_retains_complete_links_and_does_not_invent_cut_urls(self):
        content='<title>Synthetic Civic</title><a href="'+BOARD+'">Jobs</a><p>'+('x'*610000)
        d=discovery(site_mapping(content));e=resolve_employer(frame_row(),d)
        self.assertEqual(e['channels'][0]['board_key'],'greenhouse:syntheticcivic')
        self.assertTrue(e['pages'][0]['prefix_limited']);self.assertEqual(e['pages'][0]['bytes'],600000)
        self.assertIn('page_prefix_limited',{q['reason'] for q in e['review_queue']})
        content='<title>Synthetic Civic</title><p>'+('x'*599950)+'</p><a href="https://job-boards.greenhouse.io/cut-off-tenant">Jobs</a>'
        e=resolve_employer(frame_row(),discovery(site_mapping(content)))
        self.assertFalse(any(c['kind']=='supported_ats' for c in e['channels']))

    def test_camelcase_legal_name_and_logo_alt_can_corroborate_without_alias_guessing(self):
        self.assertEqual(page_identity('<title>Bright | Field Architecture</title>','BrightField Architecture LLP')['status'],'name_corroborated')
        self.assertEqual(page_identity('<title>Home</title><img alt="1234 Enterprise" src="/logo.svg">','1234 Enterprise LLC')['status'],'name_corroborated')

    def test_missing_website_identity_conflict_and_js_shell_export_review(self):
        for row,m,reason in [(frame_row(website_candidate=''),{},'no_safe_official_site'),
                    (frame_row(),site_mapping('<title>Unrelated business</title><a href="'+BOARD+'">Jobs</a>'),'identity_conflict_or_insufficient_name_evidence'),
                    (frame_row(),site_mapping('<title>Synthetic Civic</title><p>Enable JavaScript</p>'),'javascript_shell_or_sparse_page')]:
            e=resolve_employer(row,discovery(m));self.assertIn(reason,{q['reason'] for q in e['review_queue']})


class PipelineIntegrationTests(unittest.TestCase):
    def test_new_board_known_unpolled_and_previously_polled_distinct(self):
        for prior_state,expected,new in [('absent','not_matched_to_saved_registry',1),('unpolled','known_board_never_polled',0),('polled','known_board_previously_polled',0)]:
            r=empty_registry()
            if prior_state!='absent':
                register(r,BOARD,{'kind':'old_link','url':'https://old.example/'})
                if prior_state=='polled':r['boards']['greenhouse:syntheticcivic']['last_checked_run']=0
            data=run_pipeline({'rows':[frame_row()]},r,discovery(site_mapping()),collection())
            self.assertEqual(data['employers'][0]['discovery_state_before'],expected);self.assertEqual(data['metrics']['new_board_groups'],new)
            self.assertEqual(len(data['records']),2);self.assertEqual(data['metrics']['regional_posting_identities'],1)
            self.assertEqual(data['records'][0]['description'],'Original public duties. Bachelor degree. Zero to two years.')
            self.assertEqual(data['records'][1]['geography'],'elsewhere');self.assertIn('greenhouse:syntheticcivic',r['boards'])

    def test_all_existing_supported_platforms_handoff_without_new_adapters(self):
        links=['https://job-boards.greenhouse.io/new-civic', 'https://jobs.lever.co/new-civic',
               'https://jobs.ashbyhq.com/new-civic','https://new-civic.applytojob.com/apply',
               'https://careers-new-civic.icims.com/jobs/search']
        m=site_mapping('<title>Synthetic Civic</title>'+''.join('<a href="'+u+'">Jobs</a>' for u in links))
        def fake_retrieve(t,b,**kw):
            row={'identity':b['key']+':1','board_key':b['key'],'geography':'dc_dmv','location':'Washington, DC','description':'Original requirements'}
            return [row],{'board_key':b['key'],'unique':1,'descriptions':1,'dc_dmv':1,'status':'ok','errors':[]}
        registry=empty_registry()
        with patch('career_engine.employer_pipeline.retrieve_board',side_effect=fake_retrieve) as collector:
            data=run_pipeline({'rows':[frame_row()]},registry,discovery(m),collection(),board_cap=5)
        self.assertEqual(collector.call_count,5);self.assertEqual(len(data['records']),5)
        self.assertEqual({b['provider'] for b in registry['boards'].values()},{'greenhouse','lever','ashby','jazzhr','icims'})
        self.assertEqual(data['metrics']['regional_board_groups'],5)

    def test_shared_board_is_polled_once_and_entities_grouped_without_inferred_legal_employer(self):
        r=empty_registry();t=collection();data=run_pipeline({'rows':[frame_row(1),frame_row(2)]},r,discovery(site_mapping()),t)
        self.assertEqual(data['metrics']['inventories_attempted'],1);self.assertEqual(data['metrics']['entities_with_attempted_inventories'],2)
        self.assertEqual(data['records'][0]['employer_frame_entity_ids'],['external:1','external:2'])
        self.assertEqual(len(t.session.calls),2);self.assertIn('not_proof',data['records'][0]['employer_attribution'])

    def test_unverified_identity_never_enters_registry_or_collectors(self):
        r=empty_registry();t=collection();m=site_mapping('<title>Unrelated legal entity</title><a href="'+BOARD+'">Jobs</a>')
        data=run_pipeline({'rows':[frame_row()]},r,discovery(m),t)
        self.assertFalse(r['boards']);self.assertFalse(t.requests);self.assertTrue(data['employers'][0]['channels'])
        self.assertEqual(data['metrics']['verified_official_sites'],0)

    def test_discovery_global_cap_retains_all_unattempted_employers(self):
        r=empty_registry();data=run_pipeline({'rows':[frame_row(i) for i in range(30)]},r,discovery(site_mapping(),max_requests=0),collection())
        self.assertEqual(len(data['employers']),30);self.assertEqual(len(data['review_queue']),30);self.assertFalse(data['records'])
        self.assertEqual(data['metrics']['unresolved_employers'],30)

    def test_collection_stage_clock_starts_after_discovery_without_resetting_existing_budgets(self):
        clock=[0]
        with patch('career_engine.ats.time.monotonic',side_effect=lambda:clock[0]):
            d=discovery(site_mapping());get=d.session.get
            def slow_get(*args,**kw):
                if args[0]==SITE:clock[0]=20
                return get(*args,**kw)
            d.session.get=slow_get
            def factory():
                t=collection();t.max_seconds=10;return t
            data=run_pipeline({'rows':[frame_row()]},empty_registry(),d,factory)
            self.assertEqual(len(data['records']),2)
            old=collection();old.max_seconds=10;clock[0]=40
            data=run_pipeline({'rows':[frame_row()]},empty_registry(),discovery(site_mapping()),old)
            self.assertFalse(data['records']);self.assertFalse(old.requests)

    def test_collection_cap_denial_and_registry_cooldowns_do_not_erase_channels(self):
        for t in [transport(Session(),max_requests=0),transport(Session({API+'/jobs':Response({},403)}))]:
            r=empty_registry();data=run_pipeline({'rows':[frame_row()]},r,discovery(site_mapping()),t)
            self.assertFalse(data['records']);self.assertTrue(data['employers'][0]['channels']);self.assertIn('greenhouse:syntheticcivic',r['boards'])
        self.assertIn('boards-api.greenhouse.io',r['host_not_before'])
        t=collection();data=run_pipeline({'rows':[frame_row()]},r,discovery(site_mapping()),t)
        self.assertFalse(t.requests)

    def test_secondary_html_pdf_and_email_are_useful_leads_not_empty_successful_inventories(self):
        m=site_mapping('<title>Synthetic Civic</title><a href="/jobs.pdf">Jobs PDF</a><a href="https://www.idealist.org/jobs/example">Jobs</a><a href="mailto:jobs@civic.example">Apply</a>')
        r=empty_registry();t=collection();data=run_pipeline({'rows':[frame_row()]},r,discovery(m),t)
        self.assertFalse(data['inventories']);self.assertEqual(data['metrics']['unsupported_channel_leads'],3)
        self.assertEqual(data['employers'][0]['vacancy_state'],'unknown_not_zero');self.assertFalse(t.requests)

    def test_adapter_exception_keeps_other_employers_and_does_not_leak_exception_text(self):
        from career_engine.employer_pipeline import resolve_employer as real_resolve
        def resolve(row,t,page_cap):
            if row['frame_id']=='external:1':raise RuntimeError('SECRET detail should not be logged')
            return real_resolve(row,t,page_cap)
        with patch('career_engine.employer_pipeline.resolve_employer',side_effect=resolve):
            data=run_pipeline({'rows':[frame_row(1),frame_row(2)]},empty_registry(),discovery(site_mapping()),collection())
        self.assertEqual(len(data['employers']),2);self.assertEqual(len(data['records']),2)
        self.assertNotIn('SECRET',json.dumps(data));self.assertEqual(data['review_queue'][0]['reason'],'source_adapter_exception_RuntimeError')

    def test_unconventional_only_is_not_collected_and_known_site_is_not_completely_new(self):
        r=empty_registry();r['employer_sites']['site']={'url':SITE,'company_label':'Synthetic Civic'}
        d=run_pipeline({'rows':[frame_row()]},r,discovery(site_mapping('<title>Synthetic Civic</title><a href="mailto:jobs@civic.example">Apply</a>')),collection())
        self.assertEqual(d['employers'][0]['discovery_state_before'],'known_employer_site_no_board')
        self.assertEqual(d['stats']['employer_inventories']['status'],'not_collected');self.assertFalse(d['records'])
        self.assertTrue(any(s.get('channel_leads') for s in r['employer_sites'].values()))

    def test_cli_formats_base_preservation_and_atomic_state_commit(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);(p/'frame.json').write_text(json.dumps({'rows':[frame_row()]}));base={'records':[{'source':'old','source_id':'1','title':'Original row','url':'https://source.example/1'}],'stats':{}}
            (p/'base.json').write_text(json.dumps(base));original=(p/'base.json').read_bytes()
            data=run(p/'frame.json',p/'registry.json',p/'out',p/'base.json',discovery_transport=discovery(site_mapping()),collection_transport=collection())
            self.assertEqual((p/'base.json').read_bytes(),original)
            combined=json.loads((p/'out/combined-audit.json').read_text());self.assertEqual(combined['records'][0],base['records'][0]);self.assertEqual(len(combined['records']),3)
            self.assertTrue((p/'out/channel-leads.csv').exists());self.assertTrue((p/'out/review-queue.json').exists())
            prior=(p/'registry.json').read_bytes()
            with patch('tools.discover_employers.write_ledger',side_effect=OSError('disk')):
                with self.assertRaises(OSError):run(p/'frame.json',p/'registry.json',p/'out2',discovery_transport=discovery(site_mapping()),collection_transport=collection())
            self.assertEqual((p/'registry.json').read_bytes(),prior)

    def test_existing_channel_evidence_survives_a_later_page_without_that_link(self):
        r=empty_registry();run_pipeline({'rows':[frame_row()]},r,discovery(site_mapping()),collection())
        before=[c['url'] for site in r['employer_sites'].values() for c in site.get('channel_leads',[])]
        run_pipeline({'rows':[frame_row()]},r,discovery(site_mapping('<title>Synthetic Civic</title><p>Site changed.</p>')),collection())
        after=[c['url'] for site in r['employer_sites'].values() for c in site.get('channel_leads',[])]
        self.assertTrue(set(before)<=set(after))

    def test_no_ranking_model_or_legacy_collector_changes_are_required(self):
        with patch('requests.sessions.Session.request',side_effect=AssertionError('No live network in tests')):
            data=run_pipeline({'rows':[frame_row()]},empty_registry(),discovery(site_mapping()),collection())
        self.assertNotIn('score',data['records'][0]);self.assertEqual(len(data['records']),2)


if __name__=='__main__':unittest.main()
