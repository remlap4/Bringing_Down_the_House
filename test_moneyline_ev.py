from copy import deepcopy
import unittest
from unittest.mock import patch

import matching
from history import encode, sha
from moneyline_ev import evaluate_moneyline
from test_matching import binding, profile, quote


DECISION = '2026-11-17T20:02:00Z'


def prices(record, first, second):
    for selection, decimal in zip(record['selections'], (first, second)):
        selection.update(odds_text=str(decimal), odds_format='decimal', decimal_odds=decimal)


class MoneylineEVTests(unittest.TestCase):
    def setUp(self):
        self.candidate = quote('offer', 'draftkings', ('LA Clippers', 'LA Lakers'))
        self.reference = quote('reference', 'pinnacle', ('LA Lakers', 'LA Clippers'))
        self.reference['observed_at_utc'] = '2026-11-17T20:01:00+00:00'
        prices(self.candidate, 2.10, 1.80)
        prices(self.reference, 1.91, 1.91)
        self.registry = matching.read_json(matching.CONFIG / 'settlement_rules.json')

    def run_pair(self, *extra, records=None, compatible=True, **settings):
        records = [self.reference, *extra, self.candidate] if records is None else records
        settings.setdefault('as_of_utc', DECISION)
        settings.setdefault('rule_bindings', [binding(record) for record in records])
        if not compatible:
            return evaluate_moneyline(records, 'draftkings', 'draftkings', 'pinnacle', **settings)
        # Test the approval path with an isolated registry. The real pair stays incompatible.
        registry = deepcopy(self.registry)
        registry['comparisons'][0].update(full_settlement_equivalence=True,
                                          analysis_compatibility='approved')
        with patch('matching.read_json', side_effect=lambda path: registry
                   if path.name == 'settlement_rules.json' else matching_reader(path)):
            return evaluate_moneyline(records, 'draftkings', 'draftkings', 'pinnacle', **settings)

    def test_symmetric_reference_and_auditable_provenance(self):
        result = self.run_pair()
        self.assertEqual(result['reasons'], [])
        self.assertEqual(result['method'], 'proportional-v1')
        self.assertAlmostEqual(result['reference_overround'], 2 / 1.91 - 1)
        self.assertEqual(result['candidate']['age_seconds'], 120)
        self.assertEqual(result['skew_seconds'], 60)
        self.assertEqual(result['reference']['observation_sha256'], sha(encode(self.reference)))
        self.assertEqual(result['reference']['bound_rule_id'], profile('pinnacle'))
        self.assertEqual(result['reference']['source_url'], self.reference['source_url'])
        [signal] = result['signals']
        self.assertEqual(signal['team_id'], 'LAC')
        self.assertEqual(signal['reference_selection']['source_selection_id'], 'fixture-b')
        self.assertEqual(signal['fair_probability'], 0.5)
        self.assertAlmostEqual(signal['ev'], 0.05)
        self.assertAlmostEqual(result['estimates'][1]['ev'], -0.10)
        self.assertEqual(self.run_pair(min_ev=0.06)['signals'], [])

    def test_asymmetric_reference_aligns_reordered_outcomes(self):
        prices(self.reference, 1.5, 2.5)
        self.reference['selections'].reverse()
        prices(self.candidate, 2.72, 1.5)
        result = self.run_pair()
        [signal] = result['signals']
        self.assertAlmostEqual(result['reference_overround'], 1 / 15)
        self.assertAlmostEqual(signal['fair_probability'], 3 / 8)
        self.assertAlmostEqual(signal['ev'], 0.02)
        self.assertAlmostEqual(result['estimates'][1]['fair_probability'], 5 / 8)

    def assert_rejected(self, result, reason):
        self.assertTrue(any(item.startswith(reason) for item in result['reasons']), result)
        self.assertEqual(result['signals'], [])
        self.assertEqual(result['estimates'], [])

    def test_rejects_age_skew_and_started_event(self):
        self.assert_rejected(self.run_pair(max_age_seconds=59), 'stale_reference')
        self.assert_rejected(self.run_pair(max_age_seconds=119), 'stale_candidate')
        self.assert_rejected(self.run_pair(max_skew_seconds=59), 'excessive_capture_skew')
        self.assert_rejected(self.run_pair(as_of_utc='2026-11-18T00:00:00Z',
                                           max_age_seconds=14400), 'event_started')

    def test_rejects_missing_or_incompatible_rule_reviews(self):
        self.assert_rejected(self.run_pair(compatible=False), 'settlement_incompatible')
        self.assert_rejected(self.run_pair(rule_bindings=[]), 'missing_quote_rule_review')
        self.reference['market']['overtime_included'] = False
        self.assert_rejected(self.run_pair(), 'quote_rule_profile_conflict')

    def test_rejects_missing_incomplete_and_unverified_observations(self):
        self.assert_rejected(self.run_pair(records=[self.candidate]), 'missing_reference')
        self.assert_rejected(self.run_pair(records=[self.reference]), 'missing_candidate')
        self.reference['verification']['price_check'] = 'unverified'
        self.assert_rejected(self.run_pair(), 'unverified_pregame')
        self.reference['selections'].pop()
        self.assert_rejected(self.run_pair(), 'invalid_pair: complete two-selection')

    def test_latest_suspension_does_not_fall_back(self):
        suspended = deepcopy(self.reference)
        suspended.update(observation_id='suspended', observed_at_utc=DECISION)
        suspended['selections'][0].update(status='unavailable', odds_text=None, decimal_odds=None)
        self.assert_rejected(self.run_pair(suspended), 'unavailable_selection')

    def test_ambiguous_listing_and_latest_tie_are_suppressed(self):
        duplicate = deepcopy(self.reference)
        duplicate['observation_id'] = 'duplicate'
        self.assert_rejected(self.run_pair(duplicate), 'ambiguous_reference')
        duplicate['event']['source_event_id'] = 'duplicate-listing'
        self.assert_rejected(self.run_pair(duplicate), 'ambiguous_reference')

    def test_future_observations_cannot_affect_selection_or_match_context(self):
        future = deepcopy(self.reference)
        future.update(observation_id='future', observed_at_utc='2026-11-17T20:02:01Z')
        future['event']['scheduled_start_utc'] = '2026-11-19T00:00:00Z'
        self.assertEqual(self.run_pair(future), self.run_pair())
        later = self.run_pair(future, as_of_utc=future['observed_at_utc'])
        self.assert_rejected(later, 'missing_reference')
        self.assert_rejected(self.run_pair(as_of_utc='2026-11-17T20:00:59Z'), 'missing_reference')


matching_reader = matching.read_json
