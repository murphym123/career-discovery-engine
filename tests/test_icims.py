"""Synthetic transport edge cases; actual minimized HTML is tested separately."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from career_engine.ats import PublicTransport,board_from_url,posting_key,canonical_posting_url,retrieve_board,enrich_regional_metadata
from career_engine.ats_registry import empty_registry,register,source_seeds,validate_registry,discover_commoncrawl
from career_engine.discovery import geography
from career_engine.icims import index_page,detail_page,regional_searches,public_url
from career_engine.markets import DC_MARKET
from career_engine.publication_audit import build_ledger,offered_channels
from tools.expand_ats import expand
from test_ats import Session,Response,transport

ROOT='https://novel-civic.icims.com';BOARD=board_from_url(ROOT+'/jobs/search')

def html(s,status=200):return Response(status=status,raw=s.encode(),headers={'Content-Type':'text/html'})
def card(n=1,location='US-DC',extra=''):
    return f'<li class="iCIMS_JobCardItem"><span class="field-label">Job Locations</span><span>{location}</span><a href="{ROOT}/jobs/{n}/unusual-role/job"><h3>Unusual opportunity</h3></a>{extra}</li>'
def index(body=None,page=1,pages=1,next_url=''):
    return f'<title>Job Listings at Novel Civic</title><h2>Page {page} of {pages}</h2><ul class="iCIMS_JobsTable">{card() if body is None else body}</ul>'+ (f'<a rel="next" href="{next_url}">Next</a>' if next_url else '')
def detail(n=1,body='Required: writing. Training provided.'):
    return f'<link rel="canonical" href="{ROOT}/jobs/{n}/unusual-role/job"><dt>Job ID</dt><dd>2026-{n}</dd><h2>Qualifications</h2><div class="iCIMS_Expandable_Text">{body}</div><form>Candidate secrets must not enter description.</form>'
def session(mapping):return Session({ROOT+'/robots.txt':html('User-agent: *\nAllow: /jobs/'),**mapping})


class IcimsTests(unittest.TestCase):
    def test_public_routes_and_stable_identity(self):
        row=ROOT+'/jobs/12/office-support/job?in_iframe=1'
        self.assertEqual(board_from_url(row),board_from_url(BOARD['url']))
        self.assertEqual(posting_key(row),'icims:novel-civic:12')
        self.assertEqual(canonical_posting_url(row),ROOT+'/jobs/12/office-support/job')
        self.assertFalse(empty_registry()['boards'])

    def test_private_routes_and_cross_host_queries_rejected(self):
        for u in [ROOT+'/jobs/login',ROOT+'/jobs/1/role/apply',ROOT+'/api/jobs',ROOT+'/jobs/search?token=secret',ROOT+'/jobs/1/%2fadmin/job',ROOT+'/jobs/search?url=https://evil.org',ROOT.replace('icims.com','icims.com.evil.org')+'/jobs/search','https://api.icims.com/jobs/search','https://user:pass@novel-civic.icims.com/jobs/search',ROOT+':8443/jobs/search']:
            self.assertFalse(public_url(u),u);self.assertFalse(PublicTransport.allowed(u),u)

    def test_public_transport_parameters_cannot_add_private_query_keys(self):
        from career_engine.ats import FetchError
        s=session({BOARD['url']:html(index())})
        with self.assertRaises(FetchError):transport(s).get(BOARD['url'],params={'session':'secret'})
        self.assertFalse(s.calls)

    def test_free_url_index_discovers_unfamiliar_icims_from_empty_registry(self):
        def cb(url,params):
            if url.endswith('collinfo.json'):return Response([{'id':'CC-MAIN-2026-43'}])
            if params.get('showNumPages'):return Response({'pages':1})
            if 'icims.com' in params.get('url',''):
                return Response(raw=(json.dumps({'url':ROOT+'/jobs/11/research-washington-dc/job','timestamp':'20261001'})+'\n').encode())
            return Response(raw=b'')
        r=empty_registry();logs=discover_commoncrawl(transport(Session(callback=cb)),r,2,include_icims=True)
        self.assertIn(BOARD['key'],r['boards']);self.assertEqual(len(logs),2)
        self.assertEqual(r['boards'][BOARD['key']]['evidence'][0]['geography_hint'],'dc_dmv')

    def test_dc_aliases_do_not_expand_market(self):
        for loc in ['US-DC','US-DC-Washington','USA DC Washington','United States, District of Columbia, Washington','DC Washington US']:
            self.assertEqual(geography(loc),'dc_dmv',loc)
        for loc in ['Washington','US-WA-Washington','Washington County, MD','Arlington, TX','Baltimore, MD','Herdon, VA','Washington, PA','US-Remote','US-DC-Remote']:
            self.assertNotEqual(geography(loc),'dc_dmv',loc)
        self.assertEqual(DC_MARKET.asdict()['scope_id'],'dc-nearby-dmv')
        self.assertNotEqual(geography('Fredericksburg, VA'),'dc_dmv')

    def test_raw_multilocation_and_body_mention_not_worksite(self):
        rows,_=index_page(index(card(location='US-DC | US-MD-Baltimore')+card(2,'US-WA-Seattle','<div>Washington, DC research</div>')),BOARD,BOARD['url'])
        self.assertEqual(rows[0]['raw_locations'],['US-DC','US-MD-Baltimore'])
        self.assertEqual(rows[0]['worksite_evidence']['kind'],'regional_option_multiple_locations')
        self.assertEqual(rows[1]['geography'],'elsewhere')

    def test_pagination_and_description_are_distinct_denominators(self):
        nxt=BOARD['url']+'?pr=1';s=session({BOARD['url']:html(index(page=1,pages=2,next_url=nxt)),nxt:html(index(card(2),page=2,pages=2)),ROOT+'/jobs/1/unusual-role/job':html(detail())})
        rows,stats=retrieve_board(transport(s),BOARD,detail_budget=1)
        self.assertEqual(len(rows),2);self.assertTrue(stats['inventory']['metadata_complete'])
        self.assertEqual(stats['descriptions'],1);self.assertEqual(stats['inventory']['observed_available_items'],2)
        self.assertNotIn('Candidate secrets',rows[0]['description']);self.assertIn('Qualifications',rows[0]['description'])

    def test_page_cap_and_later_failure_preserve_previous_rows(self):
        nxt=BOARD['url']+'?pr=1'
        for status in [403,404,429]:
            s=session({BOARD['url']:html(index(page=1,pages=2,next_url=nxt)),nxt:html('denied',status)})
            rows,st=retrieve_board(transport(s),BOARD,detail_budget=0)
            self.assertEqual(len(rows),1);self.assertEqual(st['status'],'partial_failure');self.assertFalse(st['inventory']['metadata_complete'])
        rows,st=retrieve_board(transport(session({BOARD['url']:html(index(page=1,pages=5,next_url=nxt))})),BOARD,max_pages=1,detail_budget=0)
        self.assertEqual(len(rows),1);self.assertTrue(st['capped']);self.assertIn('pagination_page_cap',st['errors'])

    def test_shell_challenge_and_verified_empty_are_distinct(self):
        for content,expected in [('<html>Loading...</html>','failed'),('<title>Please confirm you are not a robot</title>','failed'),(index('',1,1)+'<div class="iCIMS_InfoMsg">No jobs found</div>','ok')]:
            rows,st=retrieve_board(transport(session({BOARD['url']:html(content)})),BOARD,detail_budget=0)
            self.assertFalse(rows);self.assertEqual(st['status'],expected)
            self.assertEqual(st['inventory']['verified_empty'],expected=='ok')
        _,st=retrieve_board(transport(session({BOARD['url']:html('<div>No jobs found</div><script>load()</script>')})),BOARD,detail_budget=0)
        self.assertFalse(st['inventory']['verified_empty'])

    def test_repeated_pages_and_skipped_page_numbers_not_complete(self):
        nxt=BOARD['url']+'?pr=1';s=session({BOARD['url']:html(index(page=1,pages=2,next_url=nxt)),nxt:html(index(page=2,pages=2))})
        rows,st=retrieve_board(transport(s),BOARD,detail_budget=0)
        self.assertEqual(len(rows),1);self.assertIn('repeated_index_page',st['errors'])
        _,st=retrieve_board(transport(session({BOARD['url']:html(index(page=3,pages=3))})),BOARD,detail_budget=0)
        self.assertFalse(st['inventory']['metadata_complete'])

    def test_robots_failure_delay_and_redirect_target_restriction(self):
        for robots in [html('User-agent: *\nDisallow: /jobs'),html('<html>challenge</html>'),html('failure',503)]:
            s=Session({ROOT+'/robots.txt':robots,BOARD['url']:html(index())})
            _,st=retrieve_board(transport(s),BOARD,detail_budget=0);self.assertEqual(st['requests'],1)
        pauses=[];s=Session({ROOT+'/robots.txt':html('User-agent: *\nAllow: /\nCrawl-delay: 3'),BOARD['url']:html(index())})
        retrieve_board(PublicTransport(s,pause=pauses.append),BOARD,detail_budget=0);self.assertIn(3,pauses)
        s=Session({ROOT+'/robots.txt':html('User-agent: *\nAllow: /jobs/search\nDisallow: /jobs/1'),BOARD['url']:Response({},302,{'Location':ROOT+'/jobs/1/role/job'})})
        _,st=retrieve_board(transport(s),BOARD,detail_budget=0);self.assertIn('unsafe_redirect',st['errors'])
        self.assertEqual(len(s.calls),2)

    def test_public_regional_form_option_and_missing_global_completeness(self):
        form=f'<form id="searchForm" action="{BOARD["url"]}"><select name="searchLocation"><option value="region42">US-DC</option><option value="away">US-WA</option></select></form>'
        text=index(card(2,'US-NG-Abuja'),1,10)+form;links=regional_searches(text,BOARD['url']);self.assertEqual(len(links),1)
        target=links[0]['url'];s=session({BOARD['url']:html(text),target:html(index())})
        rows,st=retrieve_board(transport(s),BOARD,detail_budget=0)
        self.assertEqual(len(rows),2);self.assertTrue(st['regional_window_complete']);self.assertFalse(st['inventory']['metadata_complete'])
        self.assertIsNone(st['inventory']['observed_available_items']);self.assertEqual(st['dc_dmv'],1)
        self.assertFalse(regional_searches(form.replace('<form ','<form method="post" '),BOARD['url']))

    def test_observed_regional_options_rotate_without_a_saved_employer_list(self):
        form=f'<form id="searchForm" action="{BOARD["url"]}"><select name="searchLocation"><option value="one">US-DC</option><option value="two">Bethesda, MD</option></select></form>'
        text=index(card(2,'US-NG-Abuja'),1,10)+form;links=regional_searches(text,BOARD['url']);board=copy.deepcopy(BOARD)
        mapping={BOARD['url']:html(text),**{x['url']:html(index()) for x in links}}
        _,first=retrieve_board(transport(session(mapping)),board,detail_budget=0)
        _,second=retrieve_board(transport(session(mapping)),board,detail_budget=0)
        self.assertNotEqual(first['selected_regional_search']['url'],second['selected_regional_search']['url'])
        self.assertEqual(second['regional_options_not_attempted'],1)

    def test_detail_wrong_identity_and_missing_sections_retain_metadata(self):
        rows,_=index_page(index(),BOARD,BOARD['url']);row=rows[0]
        for text in [detail(2),'<form>Candidate form, no job description.</form>']:
            with self.assertRaises(ValueError):detail_page(text,row)
        self.assertEqual(row['description'],'')

    def test_employer_transport_cannot_bypass_icims_route_or_robots_checks(self):
        from career_engine.employer_discovery import EmployerTransport
        from career_engine.ats import FetchError
        resolver=lambda *a,**k:[]
        t=EmployerTransport(resolver=resolver)
        self.assertFalse(t.observe(ROOT+'/jobs/1/role/apply'))
        t.resolved.add('novel-civic.icims.com')
        self.assertTrue(t.observe(BOARD['url']))
        t.robots['novel-civic.icims.com']=False
        self.assertFalse(t.redirect_allowed('novel-civic.icims.com',ROOT+'/jobs/1/role/job'))

    def test_source_edges_and_old_unsupported_registry_promoted(self):
        r=empty_registry();r['unsupported']=[{'url':ROOT+'/jobs/1/role/job','evidence':{'kind':'old_source','url':'https://publisher.org/job/1'}}]
        restored=validate_registry(r);self.assertIn(BOARD['key'],restored['boards']);self.assertFalse(restored['unsupported'])
        edges=offered_channels(f'<iframe src="{BOARD["url"]}"></iframe>','https://new-employer.org/careers')
        self.assertEqual(edges[0]['source_kind'],'public_ats_candidate')
        source_seeds(r,[{'source':'external','url':'https://publisher.org/job/1','source_id':'1','location':'Washington, DC','snippet':ROOT+'/jobs/1/role/job'}])
        self.assertIn(BOARD['key'],r['boards'])

    def test_independent_source_and_combined_audit_workflow(self):
        source={'kind':'unranked','records':[{'source':'novel','source_id':'x','company':'Unfamiliar','title':'Unusual opportunity','location':'Washington, DC','url':'https://publisher.org/x','description':'','employer_board_urls':[BOARD['url']]}],'stats':{'novel':{'status':'ok','unique':1}}}
        s=session({BOARD['url']:html(index()),ROOT+'/jobs/1/unusual-role/job':html(detail())})
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);base=p/'base.json';base.write_text(json.dumps(source));original=base.read_bytes()
            data=expand(base,p/'registry.json',p/'out',p/'combined.json',1,0,transport=transport(s),strategy='regional',extra_details=1,candidate_capture={'records':[],'stats':{},'discovery':{}})
            self.assertEqual(base.read_bytes(),original);combined=json.loads((p/'combined.json').read_text());self.assertEqual(combined['records'][0],source['records'][0])
            self.assertEqual(len(combined['records']),2);self.assertEqual(data['discovery']['platform_funnel']['icims']['described_records'],1)
            ledger=json.loads((p/'out/coverage-audit.json').read_text());b=next(x for x in ledger['sources'] if x.get('board_key')==BOARD['key']);self.assertEqual(b['retrieval_state'],'searched')
            self.assertEqual(b['inventory']['descriptions_observed'],1)

    def test_sealed_matches_never_request_description(self):
        s=session({BOARD['url']:html(index())})
        with patch('career_engine.icims.ReviewQuarantine.matches',return_value=True):
            rows,st=retrieve_board(transport(s),BOARD,detail_budget=2)
        self.assertEqual(rows[0]['detail_status'],'sealed_review_not_inspected');self.assertEqual(len(s.calls),2)


class ActualInventoryRegressionTests(unittest.TestCase):
    def test_actual_dom_variants_preserve_job_identity_and_raw_locations(self):
        fixtures=json.loads((Path(__file__).parent/'fixtures/issue21/actual-index-excerpts.json').read_text())
        self.assertEqual(len(fixtures),5)
        for case in fixtures:
            self.assertEqual(len(case['original_sha256']),64)
            self.assertTrue(case['expected'])
            board=board_from_url(case['url']);rows,info=index_page(case['html'],board,board['url'])
            self.assertEqual([{k:r[k] for k in ['identity','title','raw_locations','geography']} for r in rows],case['expected'])
            self.assertEqual(info['malformed'],0)


if __name__=='__main__':unittest.main()
