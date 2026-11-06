"""Publication mechanisms and exact scoped completeness, never market estimates."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_ats import Session,Response,transport,gh_item
from tests.test_regional_discovery import rss,xml_response,html_response
from career_engine.ats import board_from_url,retrieve_board,PublicTransport
from career_engine.ats_registry import empty_registry,register,validate_registry
from career_engine.markets import city_market,DC_MARKET
from career_engine.publication_audit import offered_channels,PublicationObserver,build_ledger,write_ledger
from tools.expand_ats import expand


class PublicationMechanismTests(unittest.TestCase):
    def test_generic_advertised_RSS_Atom_sitemap_and_ATS_channels(self):
        html='<link rel="alternate" type="application/rss+xml" title="Jobs" href="/hiring.rss"><link rel="alternate" type="application/atom+xml" href="https://new-publisher.example/jobs.atom"><iframe src="https://jobs.ashbyhq.com/unfamiliar/00000000-0000-0000-0000-000000000001"></iframe>'
        channels=offered_channels(html,'https://unfamiliar-publisher.example/jobs')
        self.assertEqual({c['source_kind'] for c in channels},{'rss','atom','public_ats_candidate'})
        self.assertTrue(any(c['url']=='https://jobs.ashbyhq.com/unfamiliar' for c in channels))
        maps=offered_channels('User-agent: *\nSitemap: http://new-publisher.example/job-sitemap.xml','https://new-publisher.example/robots.txt')
        self.assertEqual(maps[0]['url'],'https://new-publisher.example/job-sitemap.xml')

    def test_untrusted_access_bearing_links_are_not_catalogued(self):
        html='<link rel="alternate" type="application/rss+xml" href="https://user:password@example.org/feed"><link rel="alternate" type="application/rss+xml" href="https://example.org/feed?api_key=SECRET">'
        self.assertFalse(offered_channels(html,'https://example.org/'))

    def test_observer_adds_no_requests_and_returns_original_response(self):
        response=html_response('<link rel="alternate" type="application/rss+xml" href="/jobs.rss">')
        s=Session({'https://example.org/':response});observer=PublicationObserver(s)
        # This synthetic response implements the text property used by the existing collector.
        response.text=response.body.decode()
        returned=observer.get('https://example.org/')
        self.assertIs(returned,response);self.assertEqual(len(s.calls),1)
        self.assertEqual(len(observer.observations[0]['channels']),1)

    def test_actual_feed_response_is_a_discovered_mechanism_not_verified_market_coverage(self):
        channels=offered_channels('<rss><channel/></rss>','https://new-publisher.example/jobs.rss')
        self.assertEqual(channels[0]['evidence'],'received_public_feed_document')
        self.assertEqual(channels[0]['retrieval_state'],'not_yet_covered')


class ScopedCoverageLedgerTests(unittest.TestCase):
    def ledger(self,status='ok',errors=None,total=2,complete=True):
        registry=empty_registry();register(registry,'https://jobs.ashbyhq.com/new-publisher',{'kind':'observed_URL','url':'https://source.example/'})
        register(registry,'https://jobs.lever.co/not-polled',{'kind':'observed_URL','url':'https://source.example/'})
        stats={'board_key':'ashby:new-publisher','status':status,'errors':errors or [],'unique':2 if total else 0,'descriptions':1 if total else 0,'requests':1,
            'inventory':{'scope':'public endpoint response','metadata_complete':complete,'observed_available_items':total}}
        data={'captured_at':'2026-11-05T12:00:00Z','records':[],'discovery':{'boards':[stats]}}
        return build_ledger({'records':[],'stats':{}},data,registry=registry),registry

    def test_searched_and_pending_separated_with_exact_source_denominator(self):
        data,registry=self.ledger()
        entry=next(x for x in data['sources'] if x.get('board_key')=='ashby:new-publisher')
        self.assertEqual(entry['retrieval_state'],'searched')
        self.assertEqual(entry['inventory']['retained_fraction_of_observed_inventory'],1)
        pending=next(x for x in data['sources'] if x.get('board_key')=='lever:not-polled')
        self.assertEqual(pending['retrieval_state'],'not_yet_covered')
        self.assertIn('No market-wide coverage percentage',data['unknown_boundary'])
        self.assertIn('source_status_snapshots',validate_registry(registry))

    def test_partial_inventory_and_denial_have_different_states(self):
        data,_=self.ledger('partial_failure',['later_page_http_429'],total=5,complete=False)
        entry=next(x for x in data['sources'] if x.get('board_key')=='ashby:new-publisher')
        self.assertEqual(entry['retrieval_state'],'partially_retrieved');self.assertEqual(entry['inventory']['retained_fraction_of_observed_inventory'],.4)
        data,_=self.ledger('failed',['http_403'],total=0,complete=False)
        entry=next(x for x in data['sources'] if x.get('board_key')=='ashby:new-publisher')
        self.assertEqual(entry['retrieval_state'],'inaccessible')
        self.assertIsNone(entry['inventory']['retained_fraction_of_observed_inventory'])

    def test_unknown_inventory_total_never_becomes_a_market_percentage(self):
        data,_=self.ledger(total=None,complete=None)
        entry=next(x for x in data['sources'] if x.get('board_key')=='ashby:new-publisher')
        self.assertIsNone(entry['inventory']['retained_fraction_of_observed_inventory'])
        self.assertEqual(entry['retrieval_state'],'partially_retrieved')
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'ledger.json';write_ledger(path,data)
            for ext in ('json','csv','md'):self.assertTrue(path.with_suffix('.'+ext).exists())

    def test_bounded_status_history_is_preserved_across_runs(self):
        data,registry=self.ledger();base={'records':[],'stats':{}}
        for n in range(5):build_ledger(base,registry=registry)
        self.assertTrue(all(len(history)<=3 for history in registry['source_status_snapshots'].values()))

    def test_same_endpoint_discovered_by_different_mechanisms_is_one_catalogue_entry(self):
        url='https://source.example/jobs'
        base={'records':[],'stats':{},'publication_observations':[{'channels':[
            {'url':url,'source_kind':kind,'evidence':'explicit_public_document_link','discovered_from':'https://source.example/'}
            for kind in ('career_site_candidate','rss')]}]}
        data=build_ledger(base,market=city_market('Tampa','FL'))
        self.assertEqual(len(data['sources']),1)
        self.assertEqual(set(data['sources'][0]['discovery_kinds']),{'career_site_candidate','rss'})
        self.assertEqual(data['sources'][0]['retrieval_state'],'not_yet_covered')

    def test_provider_metadata_denominator_and_description_denominator_are_independent(self):
        b=board_from_url('https://job-boards.greenhouse.io/syntheticcivic');root='https://boards-api.greenhouse.io/v1/boards/syntheticcivic'
        s=Session({root:Response({'name':'Civic'}),root+'/jobs':Response({'jobs':[gh_item(),gh_item(2)],'meta':{'total':2}})})
        rows,stats=retrieve_board(transport(s),b,detail_budget=0)
        self.assertEqual(len(rows),2);self.assertTrue(stats['inventory']['metadata_complete'])
        self.assertEqual(stats['inventory']['observed_available_items'],2)
        self.assertEqual(stats['inventory']['descriptions_observed'],0)


class PortableColdStartTests(unittest.TestCase):
    def test_actual_retrieved_provider_location_conflict_remains_unknown(self):
        from career_engine.worksite_evidence import worksite_evidence
        fixture=json.loads((Path(__file__).parent/'fixtures/issue14/actual-location-conflict.json').read_text())
        self.assertEqual(len(fixture['original_response_sha256']),64)
        evidence=worksite_evidence(fixture['record'])
        self.assertEqual(evidence['worksite_evidence']['kind'],fixture['expected_worksite_kind'])
        self.assertNotEqual(evidence['geography'],'dc_dmv')

    def test_second_city_has_no_DC_employer_or_source_dependency(self):
        market=city_market('Tampa','FL');self.assertEqual(market.label('Tampa, FL'),'in_market')
        self.assertEqual(market.label('Washington, DC'),'elsewhere')
        self.assertEqual(market.label('Remote (Washington, DC, USA)'),'remote_eligibility_unknown')
        self.assertNotIn('employers',market.asdict())
        self.assertEqual(market.label('Tampa, Florida, United States'),'in_market')
        from career_engine.public_sources import url_location_hint
        self.assertEqual(url_location_hint('Office-Coordinator-Tampa',market),'possible_market')
        self.assertEqual(market.label('Tampa'),'unknown')

    def test_empty_registry_discovers_unknown_second_city_board_and_inspects_its_local_job(self):
        market=city_market('Tampa','FL');root='https://unfamiliar-suncoast.applytojob.com'
        board_html='<title>Unknown Regional Organization - Career Page</title><li class="list-group-item"><h3><a href="'+root+'/apply/AbC01234/Program-Coordinator-Tampa-FL">Program Coordinator - Tampa, FL</a></h3><ul class="list-group-item-text"><li>Tampa, FL</li></ul></li>'
        page='<h2>Program Coordinator - Tampa, FL</h2><div id="job-description">Required: clear writing. Relevant experience preferred, not required.</div>'
        mapping={'https://impactopportunity.org/robots.txt':Response({},404),'https://impactopportunity.org/rss/':xml_response(rss()),
            'https://app.jazz.co/robots.txt':Response({},404),
            'https://app.jazz.co/feeds/google/xml/0':xml_response('<urlset><url><loc>'+root+'/apply/AbC01234/Program-Coordinator-Tampa-FL</loc></url></urlset>'),
            root+'/robots.txt':Response({},404),root+'/apply':html_response(board_html),root+'/apply/AbC01234/Program-Coordinator-Tampa-FL':html_response(page)}
        s=Session(mapping)
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);src=p/'base.json';src.write_text(json.dumps({'records':[],'stats':{}}))
            self.assertFalse((p/'registry.json').exists())
            data=expand(src,p/'registry.json',p/'out',p/'collection.json',1,0,transport=transport(s),extra_details=1,
                strategy='regional',market=market,sitemap_budget=1)
            self.assertEqual(data['coverage']['in_market_labels'],1)
            self.assertEqual(data['records'][0]['geography'],'in_market')
            self.assertIn('Required: clear writing',data['records'][0]['description'])
            self.assertTrue((p/'out/coverage-audit.json').exists())
            self.assertFalse(any('senate.gov' in c[0] or 'query.wikidata' in c[0] for c in s.calls))
            ledger=json.loads((p/'out/coverage-audit.json').read_text())
            self.assertFalse(ledger['ecosystem_gaps'])  # DC audit observations are optional, not injected into another city.


if __name__=='__main__':unittest.main()
