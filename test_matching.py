import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import history
import matching

ROOT = Path(__file__).resolve().parent
START = '2026-11-18T00:00:00Z'
POSTPONED = '2026-11-21T00:00:00Z'


def quote(observation_id, source, names, start=START, source_event_id=None):
    record = matching.read_json(ROOT / 'tests/fixtures/moneyline-v1.json')
    record.update(observation_id=observation_id, source=source, bookmaker=source,
                  run_id=history.identifier(observation_id))
    if source == 'pinnacle':
        record['source_url'] = 'https://www.pinnacle.com/en/basketball/nba/matchups'
        record['market'].update(period='full_game', overtime_included=True,
                                settlement_rule_id=profile(source))
    record['event'].update(scheduled_start_utc=start, source_event_id=source_event_id or source)
    for participant, name in zip(record['event']['participants'], names):
        participant['source_name'] = name
    record['verification'].update(price_check='passed', method='synthetic test')
    return record


def profile(book):
    return f'{book}:nba:full_game:2026-11-17'


def binding(record):
    return {'observation_id': record['observation_id'],
            'observation_sha256': history.sha(history.encode(record)),
            'rule_id': profile(record['bookmaker']),
            'evidence': 'Synthetic applicability evidence for tests only'}


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.dk = quote('dk', 'draftkings', ('LA Clippers', 'LA Lakers'))
        self.pin = quote('pin', 'pinnacle', ('Los Angeles Lakers', 'Los Angeles Clippers'))

    def inspect(self, *extra, reviews=(), bindings=()):
        return matching.inspect_pair([self.dk, self.pin, *extra], 'dk', 'pin',
                                     event_reviews=reviews, rule_bindings=bindings)

    def test_reordered_participants_and_equivalent_utc_formats(self):
        self.pin['event']['scheduled_start_utc'] = '2026-11-18T00:00:00+00:00'
        result = self.inspect()
        left, right = result['quotes']
        self.assertTrue(left['canonical_event_id'])
        self.assertEqual(left['canonical_event_id'], right['canonical_event_id'])
        self.assertEqual(left['outcome_team_ids'], ['LAC', 'LAL'])
        self.assertEqual(right['outcome_team_ids'], ['LAL', 'LAC'])
        self.pin['selections'].reverse()
        self.assertEqual(self.inspect()['quotes'][1]['outcome_team_ids'], ['LAC', 'LAL'])
        self.assertEqual(result['settlement_compatibility'], 'unknown')
        self.assertFalse(result['comparable'])

    def test_treats_near_names_as_unknown_and_la_franchises_as_distinct(self):
        self.pin['event']['participants'][0]['source_name'] = 'Los Angeles Laker'
        self.assertIn('unknown_or_duplicate_team', self.inspect()['reasons'])
        self.pin['event']['participants'][0]['source_name'] = 'LA Clippers'
        self.pin['event']['participants'][1]['source_name'] = 'Toronto Raptors'
        self.assertIn('different_events', self.inspect()['reasons'])

    def test_known_roles_cannot_contradict_each_other(self):
        self.dk['event']['participants'][0]['role'] = 'home'
        self.pin['event']['participants'][0]['role'] = 'home'
        self.assertIn('conflicting_participant_roles', self.inspect()['reasons'])

    def test_duplicate_source_listings_and_distinct_schedule_slots(self):
        duplicate = quote('duplicate', 'draftkings', ('LA Lakers', 'LA Clippers'),
                          source_event_id='duplicate')
        self.assertIn('duplicate_source_listing', self.inspect(duplicate)['reasons'])
        duplicate['event']['scheduled_start_utc'] = POSTPONED
        self.assertNotIn('duplicate_source_listing', self.inspect(duplicate)['reasons'])
        self.pin['event']['scheduled_start_utc'] = POSTPONED
        self.assertIn('different_events', self.inspect(duplicate)['reasons'])

    def test_postponement_requires_review_and_preserves_original_id(self):
        original = self.inspect()['quotes'][0]['canonical_event_id']
        old = quote('old', 'pinnacle', ('LA Lakers', 'LA Clippers'))
        self.pin['event']['scheduled_start_utc'] = POSTPONED
        self.assertIn('unreviewed_schedule_or_participant_change', self.inspect(old)['reasons'])
        review = {'teams': ['LAL', 'LAC'], 'scheduled_starts': [START, POSTPONED],
                  'source_events': [
                      {'source': source, 'source_event_id': source}
                      for source in ('draftkings', 'pinnacle')],
                  'evidence': 'Synthetic postponement notice'}
        result = self.inspect(old, reviews=[review])
        self.assertEqual({item['canonical_event_id'] for item in result['quotes']}, {original})
        self.assertIn('different_scheduled_starts', result['reasons'])
        self.dk['event']['scheduled_start_utc'] = POSTPONED
        result = self.inspect(old, reviews=[review])
        self.assertEqual({item['canonical_event_id'] for item in result['quotes']}, {original})
        self.assertTrue(all(len(item['schedule_history_utc']) == 2 for item in result['quotes']))
        self.assertNotIn('different_scheduled_starts', result['reasons'])
        self.pin['event']['source_event_id'] = 'new-pinnacle-id'
        self.assertIn('unmapped_source_event', self.inspect(old, reviews=[review])['reasons'])
        review['source_events'].append({'source': 'pinnacle', 'source_event_id': 'new-pinnacle-id'})
        self.assertEqual(self.inspect(old, reviews=[review])['quotes'][1]['canonical_event_id'], original)
        self.pin['event']['scheduled_start_utc'] = '2026-11-22T00:00:00Z'
        self.assertIn('source_mapping_conflict', self.inspect(old, reviews=[review])['reasons'])

    def test_reviewed_bindings_preserve_incompatible_profiles(self):
        result = self.inspect(bindings=[binding(self.dk), binding(self.pin)])
        self.assertEqual(result['settlement_compatibility'], 'incompatible')
        self.assertEqual(result['reasons'], ['settlement_incompatible'])
        self.assertEqual([item['bound_rule_id'] for item in result['quotes']],
                         [profile('draftkings'), profile('pinnacle')])
        self.assertFalse(result['comparable'])

    def test_binding_cannot_be_reused_after_quote_change_or_for_other_book(self):
        reviewed = binding(self.pin)
        self.pin['selections'][0].update(odds_text='+200', decimal_odds=3)
        self.assertIn('quote_rule_review_digest_mismatch', self.inspect(bindings=[reviewed])['reasons'])
        reviewed = {**binding(self.pin), 'rule_id': profile('draftkings')}
        self.assertIn('quote_rule_profile_conflict', self.inspect(bindings=[reviewed])['reasons'])

    def test_identical_bound_profile_comparable_only_with_supported_market(self):
        self.pin = quote('pin', 'draftkings', ('LA Lakers', 'LA Clippers'))
        self.assertTrue(self.inspect(bindings=[binding(self.dk), binding(self.pin)])['comparable'])
        self.pin['market'].update(period='full_game', overtime_included=False)
        result = self.inspect(bindings=[binding(self.dk), binding(self.pin)])
        self.assertIn('quote_rule_profile_conflict', result['reasons'])
        self.assertFalse(result['comparable'])

    def test_cli_reads_imported_legacy_quotes_without_rewriting_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, database = root / 'archive', root / 'history.sqlite'
            for record in (self.dk, self.pin):
                record['observed_at_utc'] = '2026-11-17T20:00:00+00:00'
                record['run_id'] = history.identifier(record['source'],
                                                      record['provenance']['html_sha256'],
                                                      record['observed_at_utc'])
                record['observation_id'] = history.observation_id(record)
                history.publish_verified_batch(archive, 'test', [record], 1, [])
            before = history.ingest(archive, database)
            command = [sys.executable, str(ROOT / 'matching.py'), '--database', str(database),
                       self.dk['observation_id'], self.pin['observation_id']]
            output = subprocess.run(command, check=True, capture_output=True, text=True)
            result = json.loads(output.stdout)
            self.assertEqual(result['settlement_compatibility'], 'unknown')
            self.assertFalse(result['comparable'])
            reviews = root / 'reviews.json'
            reviews.write_bytes(history.encode({
                'events': [], 'rule_bindings': [binding(self.dk), binding(self.pin)],
            }))
            reviewed_output = subprocess.run([*command, '--reviews', str(reviews)],
                                             check=True, capture_output=True, text=True)
            self.assertEqual(json.loads(reviewed_output.stdout)['reasons'], ['settlement_incompatible'])
            self.assertEqual(history.ingest(archive, database), before)


if __name__ == '__main__':
    unittest.main()
