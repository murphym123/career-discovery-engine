"""Measurement regressions; tiny synthetic URL cases are NOT vacancy evidence."""
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('coverage_research', Path(__file__).with_name('reproduce.py'))
research = importlib.util.module_from_spec(spec)
spec.loader.exec_module(research)


class CoverageResearchTests(unittest.TestCase):
    def test_url_alias_dedup_and_distinct_requisitions(self):
        rows = [
            {'url': 'https://boards.greenhouse.io/example/jobs/10?gh_src=x', 'title': 'Assistant'},
            {'url': 'https://job-boards.greenhouse.io/example/jobs/10#apply', 'title': 'Assistant'},
            {'url': 'https://job-boards.greenhouse.io/example/jobs/11', 'title': 'Assistant'},
        ]
        self.assertEqual(sorted(map(len, research.distinct(rows))), [1, 2])
        self.assertNotEqual(research.canonical('https://example.org/jobs?id=10'),
                            research.canonical('https://example.org/jobs?id=11'))

    def test_transitive_identity_aliases(self):
        rows = [{'url': 'https://example.org/a', 'posting_identity': 'one'},
                {'url': 'https://example.org/b', 'posting_identity': 'one'},
                {'url': 'https://example.org/b'}]
        self.assertEqual(len(research.distinct(rows)), 1)

    def test_unknown_inventory_is_not_zero(self):
        result = research.summarize_inventory([
            {'observed_inventory_items': None}, {'observed_inventory_items': 0},
            {'observed_inventory_items': 4}])
        self.assertEqual(result['explicit_zero_endpoints'], 1)
        self.assertEqual(result['unknown_inventory_count'], 1)

    def test_actual_run_and_frozen_calculations_reconcile(self):
        result = research.compute()
        self.assertEqual(result, json.loads((research.ROOT / 'calculations.json').read_text()))
        self.assertEqual(result['engine']['raw_sightings'], 3288)
        self.assertEqual(result['engine']['distinct_regional_listing_identities'], 216)
        self.assertEqual(result['engine']['regional_identities_described'], 132)
        self.assertEqual(result['engine']['additional_metro_locality_identities'], 40)

    def test_missing_market_denominators_remain_null(self):
        result = research.compute()
        self.assertIsNone(result['market_coverage']['all_current_paid_vacancies'])
        self.assertIsNone(result['market_coverage']['relevant_early_career_vacancies'])
        self.assertTrue(all(r['market_coverage_estimate'] is None
                            for r in result['incompatible_ratio_illustrations_NOT_coverage']))
        self.assertTrue(all(not r['compatible_daily_denominator'] for r in research.load('benchmarks.json')))

    def test_independent_sample_is_not_a_market_sample(self):
        s = research.compute()['sample']
        self.assertEqual(list(s['strata'].values()), [4] * 8)
        self.assertEqual(s['endpoints'], s['new_checks'] + s['prior_access_controls'])
        self.assertEqual(s['offered_regional_labels'], 43)
        self.assertEqual(s['exact_engine_overlap_items'], 0)
        self.assertEqual(s['inventory_items'],
                         len(research.load('independent-inventory-records.json')) + s['protected_records_not_inspected'])
        self.assertEqual(s['protected_records_not_inspected'], 2)

    def test_publisher_ids_and_visible_counts_are_preserved(self):
        public = research.load('independent-inventory-records.json')
        self.assertEqual(len({(r['sample_id'], r['publisher_id']) for r in public}), len(public))
        inventories = research.load('independent-inventory-summary.json')
        self.assertEqual(sum(r['observed_records'] for r in inventories), 68)
        self.assertEqual(sum(r['observed_records'] for r in inventories
                             if r['denominator_basis'] in ('publisher_API_meta.total', 'public_index_Page_1_of_1')), 60)
        self.assertTrue(all(r['paid_current_vacancy_denominator'] is None for r in inventories))
        self.assertTrue(all(r['current_paid_vacancies'] is None for r in research.load('source-audit.json')))

    def test_inputs_match_manifest(self):
        self.assertEqual(research.manifest(), research.load('manifest.json'))


if __name__ == '__main__':
    unittest.main()
