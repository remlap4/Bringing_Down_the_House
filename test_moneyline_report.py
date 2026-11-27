from contextlib import redirect_stdout
import csv
from datetime import datetime
import io
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import history
import matching
import run
from test_matching import binding, quote
from test_moneyline_ev import DECISION, prices


class MoneylineReportTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.archive = self.root / 'archive'
        self.database = self.root / 'odds.sqlite'
        self.reviews = self.root / 'reviews.json'
        self.records = {}
        for source, teams in (
            ('draftkings', ('LA Clippers', 'LA Lakers')),
            ('pinnacle', ('LA Lakers', 'LA Clippers')),
        ):
            record = quote(source, source, teams)
            record['observed_at_utc'] = '2026-11-17T20:01:00+00:00'
            record['run_id'] = history.identifier(source, record['provenance']['html_sha256'],
                                                  record['observed_at_utc'])
            record['observation_id'] = history.observation_id(record)
            self.records[source] = record
        prices(self.records['draftkings'], 2.10, 1.80)
        prices(self.records['pinnacle'], 1.91, 1.91)

    def execute(self, *, failed=(), compatible=False, filename='report.csv'):
        adapters = {}
        for source, record in self.records.items():
            adapter = Mock(source=source)
            if source in failed:
                adapter.capture.side_effect = TimeoutError('navigation timed out')
            adapter.archive.side_effect = (
                lambda snapshot, archive, device, evidence, record=record:
                history.publish_verified_batch(archive, device, [record], 1, [])
            )
            adapters[source] = adapter
        self.reviews.write_text(json.dumps({
            'events': [], 'rule_bindings': [binding(record) for record in self.records.values()],
        }), encoding='utf-8')
        read_json = matching.read_json
        registry = read_json(matching.CONFIG / 'settlement_rules.json')
        if compatible:
            registry['comparisons'][0].update(full_settlement_equivalence=True,
                                              analysis_compatibility='approved')

        output = self.root / filename
        stdout = io.StringIO()
        with (patch('run.Path.cwd', return_value=self.root),
              patch.dict(run.os.environ),
              patch('run.PinnacleAdapter', return_value=adapters['pinnacle']),
              patch('run.DraftKingsAdapter', return_value=adapters['draftkings']),
              patch('moneyline_report.datetime', wraps=datetime) as clock,
              patch('matching.read_json', side_effect=lambda path:
                    registry if path.name == 'settlement_rules.json' else read_json(path)),
              redirect_stdout(stdout)):
            clock.now.return_value = datetime.fromisoformat(DECISION)
            status = run.main(['moneyline', '--sport', 'nba', '--headless',
                               '--database', str(self.database), '--archive', str(self.archive),
                               '--csv', str(output), '--reviews', str(self.reviews)])
        with output.open(encoding='utf-8', newline='') as handle:
            rows = list(csv.DictReader(handle))
        return status, stdout.getvalue(), rows

    def test_incompatible_pair_stores_history_and_reports_provenance(self):
        status, terminal, rows = self.execute()
        self.assertEqual(status, 0)
        self.assertIn('settlement_incompatible', terminal)
        self.assertEqual(rows[-1]['status'], 'no_eligible_comparisons')
        self.assertEqual(rows[2]['status'], 'rejected')
        self.assertEqual(rows[2]['ev'], '')
        reference = json.loads(rows[2]['reference_quote'])
        self.assertEqual(reference['observation_sha256'],
                         history.sha(history.encode(self.records['pinnacle'])))
        self.assertTrue(reference['source_url'])
        self.assertEqual(reference['verification']['price_check'], 'passed')
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM imports').fetchone(), (2,))
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM observations').fetchone(), (2,))

    def test_reference_failure_does_not_use_existing_history(self):
        self.execute(compatible=True)
        status, terminal, rows = self.execute(failed=('pinnacle',), compatible=True,
                                              filename='partial.csv')
        self.assertEqual(status, 1)
        self.assertIn('navigation timed out', terminal)
        self.assertEqual(rows[0]['phase'], 'capture')
        self.assertEqual(rows[2]['reason'], 'missing_reference')
        self.assertEqual(rows[2]['reference_quote'], 'null')
        self.assertEqual(json.loads(rows[2]['candidate_quotes'])[0]['source'], 'draftkings')
        self.assertEqual(rows[-1]['status'], 'partial_collection_failure')

    def test_total_failure_still_writes_audit_csv(self):
        status, _, rows = self.execute(failed=('pinnacle', 'draftkings'))
        self.assertEqual(status, 1)
        self.assertEqual([row['status'] for row in rows], ['failed', 'failed', 'collection_failed'])
        self.assertFalse(self.database.exists())
        self.assertFalse(self.archive.exists())

    def test_compatible_estimates_and_below_threshold_summary(self):
        status, terminal, rows = self.execute(compatible=True)
        self.assertEqual(status, 0)
        self.assertIn('1 qualifying outcomes', terminal)
        self.assertEqual(rows[2]['status'], 'qualifying')
        self.assertEqual(rows[2]['team_id'], 'LAC')
        self.assertEqual(float(rows[2]['fair_probability']), 0.5)
        self.assertAlmostEqual(float(rows[2]['ev']), 0.05)
        self.assertEqual(rows[3]['status'], 'below_threshold')

        candidate = self.records['draftkings']
        candidate['provenance']['html_sha256'] = history.sha(b'changed synthetic capture')
        candidate['run_id'] = history.identifier(
            'draftkings', candidate['provenance']['html_sha256'], candidate['observed_at_utc'])
        candidate['observation_id'] = history.observation_id(candidate)
        prices(candidate, 2.01, 1.80)
        status, _, rows = self.execute(compatible=True, filename='below-threshold.csv')
        self.assertEqual(status, 0)
        self.assertEqual(rows[-1]['status'], 'no_qualifying_outcomes')
