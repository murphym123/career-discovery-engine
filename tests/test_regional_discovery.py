"""Synthetic retrieval/selection contracts; actual minimized examples are separate."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_ats import Response,Session,transport,gh_item
from career_engine.ats import board_from_url,posting_key,PublicTransport,FetchError
from career_engine.ats_registry import empty_registry,register,validate_registry,source_seeds
from career_engine.employer_discovery import EmployerTransport,directory_query,discover_directory,discover_employer_links,website_url,careers_links
from career_engine.public_sources import impact_records,retrieve_impact,discover_jazz_sitemap,public_text,url_location_hint
from career_engine.regional_selection import select_regional_boards,evidence_strength,mark_seen
from career_engine.jazzhr import jazz_index,jazz_detail,retrieve_jazzhr
from career_engine.worksite_evidence import worksite_evidence
from career_engine.ats_coverage import assess_increment
from tools.expand_ats import expand

ROOT=Path(__file__).resolve().parents[1]

def resolver(host,port,**kw):return [(2,1,6,'',('93.184.216.34',port))]

def xml_response(text):return Response(raw=text.encode(),headers={'Content-Type':'application/xml'})

def html_response(text):return Response(raw=text.encode(),headers={'Content-Type':'text/html'})

def rss():
    return '''<rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><item>
    <title>Unusual civic opportunity</title><link>https://impactopportunity.org/job/123/unusual-civic-opportunity/</link>
    <dc:creator>Example advertiser (may be recruiter)</dc:creator><description><![CDATA[
    Hybrid (Washington, DC, USA)
    Example advertiser
    <p>Required: bachelor degree and one year research experience.</p>
    <a href="https://jobs.lever.co/never-configured/00000000-0000-0000-0000-000000000001">Careers</a>
    ]]></description></item></channel></rss>'''

def jazz_html():
    return '''<html><title>Unfamiliar Civic Group - Career Page</title><ul class="list-group">
    <li class="list-group-item"><h3><a href="https://novelcivic.applytojob.com/apply/AbC01234/Unusual-Role">Unusual Role</a></h3><ul class="list-group-item-text"><li>Washington, DC</li></ul></li>
    <li class="list-group-item"><h3><a href="https://novelcivic.applytojob.com/apply/XyZ12345/Other-Role">Other Role</a></h3><ul class="list-group-item-text"><li>London, UK</li></ul></li>
    </ul></html>'''


class PublicFeedAndSitemapTests(unittest.TestCase):
    def test_RSS_full_requirements_and_advertiser_location_provenance(self):
        rows=impact_records(rss());self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['geography'],'dc_dmv');self.assertIn('one year research',rows[0]['description'])
        self.assertIn('may_be_recruiter',rows[0]['company_evidence']);self.assertEqual(rows[0]['source_id'],'123')
        self.assertEqual(len(rows[0]['employer_board_urls']),1)

    def test_feed_denial_or_robots_failure_does_not_become_no_jobs_success(self):
        s=Session({'https://impactopportunity.org/robots.txt':Response({},403)})
        rows,stats=retrieve_impact(transport(s));self.assertFalse(rows);self.assertEqual(stats['status'],'failed')
        self.assertEqual(len(s.calls),1)

    def test_XML_entities_are_rejected_and_empty_schema_is_visible(self):
        with self.assertRaises(ValueError):impact_records('<!DOCTYPE a [<!ENTITY b SYSTEM "file:///etc/passwd">]><rss/>')
        s=Session({'https://impactopportunity.org/robots.txt':Response({},404),'https://impactopportunity.org/rss/':xml_response('<rss><channel/></rss>')})
        rows,stats=retrieve_impact(transport(s));self.assertFalse(rows);self.assertEqual(stats['status'],'failed')

    def test_sitemap_prefix_contributes_only_complete_observed_board_URLs(self):
        s=Session({'https://app.jazz.co/robots.txt':Response({},404),'https://app.jazz.co/feeds/google/xml/0':xml_response('<urlset><url><loc>https://novelcivic.applytojob.com/apply/AbC01234/Policy-Assistant-Washington-DC</loc></url><url><loc>https://incomplete.applytojob.com/')})
        r=empty_registry();logs=discover_jazz_sitemap(transport(s),r,1)
        self.assertEqual(logs[0]['complete_URL_entries'],1);self.assertEqual(set(r['boards']),{'jazzhr:novelcivic'})
        self.assertEqual(r['boards']['jazzhr:novelcivic']['evidence'][0]['geography_hint'],'dc_dmv')
        self.assertEqual(r['jazz_sitemap_offset'],1)
        self.assertEqual(url_location_hint('Legal-Internship-NYC-And-DC'),'dc_dmv')
        self.assertNotIn('records',logs[0])  # URL seeds are not vacancies.

    def test_truncation_is_opt_in_and_audited(self):
        url='https://app.jazz.co/feeds/google/xml/0'
        t=transport(Session({url:xml_response('<urlset>'+('x'*2000))}))
        body=t.get(url,max_bytes=100,kind='seed_sitemap',prefix=True)
        self.assertEqual(len(body),100);self.assertTrue(t.requests[0]['truncated'])
        self.assertEqual(t.requests[0]['sha256_kind'],'retrieved_prefix_only')
        with self.assertRaises(FetchError):transport(Session({url:xml_response('x'*2000)})).get(url,max_bytes=100,kind='seed_sitemap')

    def test_feed_cannot_request_login_application_or_paid_endpoints(self):
        for url in ['https://app.jazz.co/api/private','https://impactopportunity.org/application-redirect/123','https://query.wikidata.org/private','https://novelcivic.applytojob.com/cb','https://novelcivic.applytojob.com/apply/AbC01234/Unusual-Role/submit']:
            self.assertFalse(PublicTransport.allowed(url),url)


class JazzPublicCareerTests(unittest.TestCase):
    def test_public_board_namespaces_case_sensitive_ids_and_observed_metadata(self):
        b=board_from_url('https://novelcivic.applytojob.com/apply/AbC01234/Unusual-Role')
        self.assertEqual(b['provider'],'jazzhr');self.assertEqual(b['url'],'https://novelcivic.applytojob.com/apply')
        self.assertEqual(posting_key(b['url']+'/AbC01234/Unusual-Role'),'jazzhr:novelcivic:AbC01234')
        rows=jazz_index(jazz_html(),b);self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['company'],'Unfamiliar Civic Group');self.assertEqual(rows[0]['geography'],'dc_dmv')
        self.assertFalse(rows[1]['description']);self.assertEqual(rows[1]['geography'],'elsewhere')

    def test_JSON_LD_detail_retained_and_wrong_job_does_not_replace_metadata(self):
        b=board_from_url('https://novelcivic.applytojob.com/apply');row=jazz_index(jazz_html(),b)[0]
        body={'@context':'https://schema.org','@type':'JobPosting','title':row['title'],'description':'<p>Required research experience and clear writing.</p>',
              'url':row['url'],'hiringOrganization':{'name':'Unfamiliar Civic Group'},'jobLocation':{'address':{'addressLocality':'Washington','addressRegion':'DC','addressCountry':'US'}}}
        jazz_detail('<script type="application/ld+json">'+json.dumps(body)+'</script>',row)
        self.assertIn('Required research',row['description']);self.assertEqual(row['geography'],'dc_dmv')
        body['url']=row['url'].replace('AbC01234','wrong1234')
        with self.assertRaises(ValueError):jazz_detail('<script type="application/ld+json">'+json.dumps(body)+'</script>',row)

    def test_older_public_HTML_section_supports_requirements_and_ignores_form_state(self):
        b=board_from_url('https://novelcivic.applytojob.com/apply');row=jazz_index(jazz_html(),b)[0]
        html='<h2>Unusual Role</h2><div id="job-description">Required: strong communication. Training provided.</div><form><input value="SECRET_FORM_VALUE"></form>'
        jazz_detail(html,row)
        self.assertIn('Training provided',row['description']);self.assertNotIn('SECRET_FORM',row['description'])
        self.assertEqual(row['description_format'],'public_JazzHR_HTML_job_description_section')

    def test_explicit_volunteer_notice_keeps_record_but_excludes_employment_gain(self):
        b=board_from_url('https://novelcivic.applytojob.com/apply');row=jazz_index(jazz_html(),b)[0]
        jazz_detail('<h2>Unusual Role</h2><div id="job-description">Paid Board: No (all roles are voluntary). Residency required.</div>',row)
        metrics=assess_increment([],[row]);self.assertEqual(len([row]),1)
        self.assertEqual(metrics['dc_dmv_labels'],1);self.assertEqual(metrics['dc_not_matched_to_five_sources'],0)
        self.assertEqual(metrics['explicit_volunteer_appointments'],1)

    def test_explicit_future_opening_notice_is_retained_as_pool(self):
        b=board_from_url('https://novelcivic.applytojob.com/apply');row=jazz_index(jazz_html(),b)[0]
        row['title']='Consideration for Future Openings - Example City'
        jazz_detail('<h2>'+row['title']+'</h2><div id="job-description">We will contact qualified applicants as we approach our recruitment process.</div>',row)
        self.assertEqual(row['record_kind'],'registration_pool')
        self.assertEqual(assess_increment([],[row])['dc_not_matched_to_five_sources'],0)

    def test_metadata_survives_detail_failure_and_nonlocal_metadata_is_retained(self):
        b=board_from_url('https://novelcivic.applytojob.com/apply')
        s=Session({'https://novelcivic.applytojob.com/robots.txt':Response({},404),b['url']:html_response(jazz_html())})
        rows,stats=retrieve_jazzhr(transport(s),b,1)
        self.assertEqual(len(rows),2);self.assertEqual(stats['status'],'partial_failure')
        self.assertFalse(any('/XyZ12345/' in c[0] for c in s.calls))


class DirectoryAndGraphTests(unittest.TestCase):
    def test_query_is_regional_not_a_company_list_and_rotates(self):
        q=directory_query(200);self.assertIn('wdt:P159 wd:Q61',q);self.assertIn('OFFSET 200',q)
        self.assertNotIn('FILTER',q);self.assertNotIn('VALUES ?org',q)
        with self.assertRaises(ValueError):directory_query(-1)

    def test_directory_discovers_arbitrary_organization_and_records_weak_HQ_claim(self):
        r=empty_registry();s=Session(callback=lambda u,p:Response({'results':{'bindings':[{'org':{'value':'http://www.wikidata.org/entity/Q999'},'orgLabel':{'value':'New Community Institute'},'website':{'value':'https://new-community.example/'}}]}}))
        log=discover_directory(transport(s),r)
        self.assertEqual(log['new_employer_sites'],1);site=next(iter(r['employer_sites'].values()))
        self.assertEqual(site['headquarters_claim'],'Washington, DC');self.assertFalse(r['boards'])
        self.assertEqual(r['directory_offset'],0)

    def test_observed_public_graph_discovers_unknown_tenant_without_guessing(self):
        r=empty_registry();r['employer_sites']['x']={'url':'https://new-community.example/','company_label':'New Community Institute','evidence_url':'https://www.wikidata.org/entity/Q999','headquarters_claim':'Washington, DC','last_checked_run':None}
        s=Session({'https://new-community.example/robots.txt':Response({},404),
                   'https://new-community.example/':html_response('<a href="/our-team/opportunities">Careers</a>'),
                   'https://new-community.example/our-team/opportunities':html_response('<iframe src="https://jobs.ashbyhq.com/entirely-unfamiliar-tenant"></iframe>')})
        t=EmployerTransport(s,resolver=resolver,pause=lambda _:None);log=discover_employer_links(t,r,[],1)
        self.assertIn('ashby:entirely-unfamiliar-tenant',r['boards']);self.assertEqual(log[0]['observed_ATS_boards'],1)
        evidence=r['boards']['ashby:entirely-unfamiliar-tenant']['evidence'][0]
        self.assertIn('headquarters',evidence['note']);self.assertFalse(any('careers' in c[0] for c in s.calls))

    def test_private_DNS_credentials_unobserved_and_known_restrictions_not_requested(self):
        t=EmployerTransport(Session(),resolver=lambda *a,**k:[(2,1,6,'',('127.0.0.1',443))],pause=lambda _:None)
        self.assertFalse(t.observe('https://public-name.example/'));self.assertFalse(t.allowed('https://public-name.example/'))
        self.assertFalse(t.observe('https://pac.org/'));self.assertFalse(website_url('https://user:secret@example.org/'))
        self.assertFalse(website_url('file:///etc/passwd'));self.assertFalse(t.allowed('https://unobserved.example/'))

    def test_www_redirect_must_still_be_public_and_other_company_redirect_rejected(self):
        t=EmployerTransport(Session(),resolver=resolver,pause=lambda _:None);t.observe('https://new-community.example/')
        self.assertTrue(t.redirect_allowed('new-community.example','https://www.new-community.example/'))
        self.assertFalse(t.redirect_allowed('new-community.example','https://other-company.example/'))
        self.assertFalse(t.redirect_allowed('new-community.example','https://new-community.example/login'))

    def test_robots_and_stricter_delay_stop_before_homepage(self):
        for rules in ['User-agent: *\nDisallow: /','User-agent: *\nCrawl-delay: 10']:
            s=Session({'https://new-community.example/robots.txt':Response(raw=rules.encode(),headers={'Content-Type':'text/plain'})})
            t=EmployerTransport(s,resolver=resolver,pause=lambda _:None);t.observe('https://new-community.example/')
            with self.assertRaises(FetchError):public_text(t,'https://new-community.example/',kind='public_html')
            self.assertEqual(len(s.calls),1)


class RegionalSelectionAndHistoryTests(unittest.TestCase):
    def registry(self):
        r=empty_registry()
        for n in range(100):
            register(r,f'https://jobs.ashbyhq.com/unknown-{n}',{'kind':'index','url':'https://index.example/'})
        for n in range(10):
            register(r,f'https://jobs.ashbyhq.com/unknown-{n}',{'kind':'source_audit_link','url':f'https://source.example/{n}','location':'Washington, DC','job_title':'Unfamiliar Policy Program Opportunity'})
        return r

    def test_reserves_non_targeted_exploration_and_keeps_unfamiliar_employers_primary(self):
        r=self.registry()
        with patch('career_engine.triage.triage',side_effect=AssertionError('No candidate ranking in discovery')):
            chosen=select_regional_boards(r,30)
        self.assertEqual(len(chosen),30);self.assertEqual(len({e['key'] for e in chosen}),30)
        routes=[e['selection_evidence']['route'] for e in chosen]
        self.assertEqual(routes.count('non_targeted_exploration'),6)
        self.assertGreaterEqual(sum(evidence_strength(e)==6 for e in chosen),10)
        self.assertTrue(all(e['last_checked_run'] is None for e in chosen))
        self.assertIn('non_targeted_exploration',routes[:3])

    def test_unscoped_function_URLs_cannot_take_all_remaining_board_slots(self):
        r=self.registry()
        for n,e in enumerate(r['boards'].values()):
            if n>=10:register(r,e['url'],{'kind':'public_provider_sitemap','job_title_hint':'Program Coordinator','geography_hint':'unknown','url':'https://app.jazz.co/feeds/google/xml/0'})
        chosen=select_regional_boards(r,30)
        self.assertLessEqual(sum(e['selection_evidence']['route']=='weak_function_exploration' for e in chosen),7)
        self.assertEqual(sum(e['selection_evidence']['route']=='non_targeted_exploration' for e in chosen),6)

    def test_selection_reproducible_without_employer_brand_or_candidate_profile(self):
        r=self.registry();a=select_regional_boards(copy.deepcopy(r));b=select_regional_boards(copy.deepcopy(r))
        self.assertEqual([e['key'] for e in a],[e['key'] for e in b])
        with self.assertRaises(ValueError):select_regional_boards(r,30,.19)

    def test_headquarters_does_not_establish_job_function_or_worksite(self):
        b={'evidence':[{'kind':'regional_employer_public_link','geography_hint':'employer_headquarters_only','location':'Washington, DC'}]}
        self.assertEqual(evidence_strength(b),3)
        b['company']='Famous Policy Employer';self.assertEqual(evidence_strength(b),3)

    def test_high_value_actual_source_evidence_survives_URL_evidence_cap(self):
        r=empty_registry();url='https://jobs.ashbyhq.com/unknown'
        for n in range(30):register(r,url,{'kind':'index','url':str(n)})
        register(r,url,{'kind':'source_audit_link','url':'https://source.example/new','location':'Washington, DC','job_title':'Program Assistant'})
        b=r['boards']['ashby:unknown'];self.assertEqual(len(b['evidence']),20);self.assertEqual(evidence_strength(b),6)

    def test_new_vs_previously_seen_counts_source_identities_not_within_run_sightings(self):
        r=empty_registry();r['run']=1;a={'identity':'source:x','retrieved_at':'now','description_sha256':'old'}
        first=mark_seen(r,[a,copy.deepcopy(a)]);self.assertEqual(first['first_observed_by_registry'],1);self.assertEqual(first['previously_observed'],0)
        r=validate_registry(r);r['run']=2;b={**a,'description_sha256':'new'};second=mark_seen(r,[b])
        self.assertTrue(b['previously_seen_in_this_registry']);self.assertTrue(b['description_changed_since_prior_observation'])
        self.assertEqual(second['first_observed_by_registry'],0);self.assertEqual(second['previously_observed'],1)

    def test_location_segments_prevent_cross_state_false_positive(self):
        row={'location':'Arlington, TX ; Richmond, VA','raw_locations':['Arlington, TX','Richmond, VA']}
        evidence=worksite_evidence(row);self.assertNotEqual(evidence['geography'],'dc_dmv')
        row={'location':'New York, NY ; Washington, DC','raw_locations':['New York, NY','Washington, DC']}
        self.assertEqual(worksite_evidence(row)['worksite_evidence']['kind'],'regional_option_multiple_locations')
        row={'location':'Remote (Washington, DC, USA)','raw_locations':['Remote (Washington, DC, USA)']}
        self.assertEqual(worksite_evidence(row)['worksite_evidence']['kind'],'remote_with_regional_label')
        conflict={'title':'Expert Tour Guide - Montreal Opportunities','location':'Washington DC, DC','raw_locations':['Washington DC, DC']}
        self.assertEqual(worksite_evidence(conflict)['geography'],'unknown')
        self.assertEqual(worksite_evidence({'location':'DC'})['geography'],'dc_dmv')

    def test_combined_audit_keeps_original_ATS_and_RSS_rows_and_independent_stats(self):
        b=board_from_url('https://jobs.ashbyhq.com/new-org');registry=empty_registry();register(registry,b['url'],{'kind':'source_audit_link','url':'https://source.example/','location':'Washington, DC','job_title':'Program Associate'})
        item={'title':'Atypical Opportunity','jobUrl':b['url']+'/00000000-0000-0000-0000-000000000001','location':'Washington, DC','descriptionPlain':'Public original requirements.'}
        s=Session({'https://api.ashbyhq.com/posting-api/job-board/new-org':Response({'jobs':[item]})})
        supplemental={'records':impact_records(rss()),'stats':{'impactopportunity':{'status':'ok','errors':[]}},'discovery':{}}
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);src=p/'base.json';src.write_text(json.dumps({'records':[{'source':'senate','source_id':'1','url':'https://source.example/1','title':'Old role','company':'Old org','location':'Washington, DC'}],'stats':{'senate':{'status':'ok','errors':[]}}}));original=src.read_bytes()
            (p/'registry.json').write_text(json.dumps(registry))
            expand(src,p/'registry.json',p/'ats',p/'combined.json',1,0,transport=transport(s),strategy='regional',candidate_capture=supplemental)
            combined=json.loads((p/'combined.json').read_text());self.assertEqual(len(combined['records']),3);self.assertEqual(src.read_bytes(),original)
            self.assertEqual(set(combined['stats']),{'senate','public_ats','impactopportunity'})
            self.assertEqual(len(json.loads((p/'ats/ats-source-audit.json').read_text())['records']),1)
            self.assertEqual(len(json.loads((p/'ats/regional-source-audit.json').read_text())['records']),1)




class FrozenComparisonSafetyTests(unittest.TestCase):
    def test_snapshot_integrity_and_no_live_fallback(self):
        from tools.compare_discovery import FrozenSession
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);body=b'{"jobs":[]}';(p/'body').write_bytes(body)
            entry={'url':'https://api.ashbyhq.com/posting-api/job-board/test','params':{},'status':200,'headers':{'Content-Type':'application/json'},'complete':True,'body_file':'body','sha256':hashlib.sha256(body).hexdigest()}
            manifest=p/'responses.json';manifest.write_text(json.dumps({'responses':[entry]}));s=FrozenSession(manifest)
            with patch('requests.sessions.Session.request',side_effect=AssertionError('No network permitted')):
                self.assertEqual(b''.join(s.get(entry['url']).iter_content(4)),body)
                with self.assertRaises(AssertionError):s.get(entry['url']+'/missing')
            (p/'body').write_bytes(b'changed')
            with self.assertRaises(ValueError):FrozenSession(manifest)

    def test_incomplete_payload_never_becomes_a_complete_job_response(self):
        from tools.compare_discovery import FrozenResponse
        import requests
        r=FrozenResponse({'status':200,'headers':{},'complete':False},b'{"jobs":[')
        with self.assertRaises(requests.exceptions.ChunkedEncodingError):b''.join(r.iter_content(100))

    def test_payload_path_cannot_escape_snapshot_folder(self):
        from tools.compare_discovery import FrozenSession
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);(p/'sub').mkdir();(p/'body').write_bytes(b'[]')
            entry={'url':'https://example.org/','status':200,'headers':{},'complete':True,'body_file':'../body','sha256':hashlib.sha256(b'[]').hexdigest()}
            manifest=p/'sub/manifest.json';manifest.write_text(json.dumps({'responses':[entry]}))
            with self.assertRaises(ValueError):FrozenSession(manifest)


if __name__=='__main__':unittest.main()
