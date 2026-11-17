from copy import deepcopy
import json
from pathlib import Path
import unittest

from moneyline_contract import ContractError, validate_observation

FIXTURE = Path(__file__).parent / 'tests' / 'fixtures' / 'moneyline-v1.json'


class MoneylineContractTests(unittest.TestCase):
    def setUp(self):
        self.record = json.loads(FIXTURE.read_text())

    def test_unresolved_historical_record_is_valid_without_becoming_verified(self):
        original = deepcopy(self.record)
        validate_observation(self.record)
        self.assertEqual(self.record, original)
        self.assertIsNone(self.record['market']['overtime_included'])
        self.assertEqual(self.record['verification']['price_check'], 'unverified')

    def test_decimal_source_and_unavailable_selection(self):
        a, b = self.record['selections']
        a.update(odds_format='decimal', odds_text='2.480', decimal_odds=2.48)
        b.update(status='unavailable', odds_text=None, decimal_odds=None)
        validate_observation(self.record)

    def test_missing_unknown_and_future_schema_fields_fail(self):
        for change in ('missing', 'extra', 'version', 'boolean_version'):
            with self.subTest(change=change):
                record = deepcopy(self.record)
                if change == 'missing': del record['source']
                if change == 'extra': record['ev'] = 0.1
                if change == 'version': record['schema_version'] = 2
                if change == 'boolean_version': record['schema_version'] = True
                with self.assertRaises(ContractError): validate_observation(record)

    def test_timestamps_require_utc_and_causal_order(self):
        changes = [
            ('observed_at_utc', '2026-11-17T20:00:00'),
            ('observed_at_utc', '2026-11-17T15:00:00-05:00'),
            ('source_updated_at_utc', '2026-11-17T20:01:00Z'),
        ]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                record = deepcopy(self.record)
                record[key] = value
                with self.assertRaises(ContractError): validate_observation(record)
        self.record['provenance']['capture_started_at_utc'] = '2026-11-17T20:01:00Z'
        with self.assertRaises(ContractError): validate_observation(self.record)

    def test_pregame_start_must_be_future_but_live_can_be_past(self):
        self.record['event']['scheduled_start_utc'] = '2026-11-17T19:00:00Z'
        with self.assertRaises(ContractError): validate_observation(self.record)
        self.record['event']['status'] = 'live'
        validate_observation(self.record)

    def test_incomplete_or_duplicate_outcomes_rejected(self):
        for change in ('missing_side', 'duplicate_side', 'boolean_index', 'duplicate_id'):
            with self.subTest(change=change):
                record = deepcopy(self.record)
                if change == 'missing_side': record['selections'].pop()
                if change == 'duplicate_side': record['selections'][1]['participant_index'] = 0
                if change == 'boolean_index': record['selections'][1]['participant_index'] = True
                if change == 'duplicate_id': record['selections'][1]['source_selection_id'] = 'fixture-a'
                with self.assertRaises(ContractError): validate_observation(record)

    def test_duplicate_participants_rejected(self):
        self.record['event']['participants'][1]['source_name'] = ' fixture AWAY '
        with self.assertRaises(ContractError): validate_observation(self.record)

    def test_nonfinite_boolean_or_mismatched_prices_rejected(self):
        for odds in (float('nan'), float('inf'), 1, True, -2, 2.6):
            with self.subTest(odds=odds):
                record = deepcopy(self.record)
                record['selections'][0]['decimal_odds'] = odds
                with self.assertRaises(ContractError): validate_observation(record)

    def test_suspended_price_cannot_be_available(self):
        self.record['selections'][0]['status'] = 'unavailable'
        with self.assertRaises(ContractError): validate_observation(self.record)

    def test_spreads_and_partial_periods_cannot_enter_contract(self):
        for key, value in (('line', -4.5), ('type', 'spread'), ('period', 'first_half')):
            with self.subTest(key=key):
                record = deepcopy(self.record)
                record['market'][key] = value
                with self.assertRaises(ContractError): validate_observation(record)

    def test_reviewed_rules_require_explicit_semantics(self):
        self.record['market']['rule_status'] = 'reviewed'
        with self.assertRaises(ContractError): validate_observation(self.record)
        self.record['market'].update(settlement_rule_id='fixture-rules', period='full_game', overtime_included=True)
        validate_observation(self.record)

    def test_verification_cannot_claim_pass_without_method(self):
        self.record['verification']['price_check'] = 'passed'
        with self.assertRaises(ContractError): validate_observation(self.record)

    def test_provenance_must_be_portable_and_traceable(self):
        for path in ('/Users/test/page.html', '../page.html', 'C:\\data\\page.html'):
            with self.subTest(path=path):
                record = deepcopy(self.record)
                record['provenance']['evidence_path'] = path
                with self.assertRaises(ContractError): validate_observation(record)
        self.record['provenance']['html_sha256'] = 'unknown'
        with self.assertRaises(ContractError): validate_observation(self.record)


if __name__ == '__main__':
    unittest.main()
