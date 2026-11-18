import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import history
import test_scraper


class HistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.archive = self.root / 'archive'
        self.database = self.root / 'local.sqlite'
        self.snapshot = self.root / 'snapshot'
        self.snapshot.mkdir()
        event = test_scraper.EventTests()
        self.html = event.fixture(event.start())
        self.visible = ('OKC Thunder\nAT\nCLE Cavaliers\n+100\n+100\n+150\n'
                        '+100\n+100\n−179\nToday 7:40 PM\nMore Bets')
        self.metadata = {**event.meta, 'capture_started_at_utc': '2026-11-07T22:59:00+00:00',
                         'html_sha256': history.sha(self.html.encode('utf-8'))}
        (self.snapshot / 'page.html').write_text(self.html, encoding='utf-8')
        (self.snapshot / 'visible.txt').write_text(self.visible, encoding='utf-8')

    def publish(self, supersedes=None):
        (self.snapshot / 'metadata.json').write_text(json.dumps(self.metadata), encoding='utf-8')
        return history.archive_snapshot(self.snapshot, self.archive, 'test-device',
                                        'data/snapshot/page.html', supersedes)

    def test_reimport_later_prices_and_clean_rebuild(self):
        first = self.publish()
        original = history.ingest(self.archive, self.database)
        self.assertEqual(self.publish(), first)
        self.assertEqual(history.ingest(self.archive, self.database), original)
        self.metadata['captured_at_utc'] = '2026-11-07T23:01:00Z'
        self.assertNotEqual(self.publish(), first)
        summary = history.ingest(self.archive, self.database)
        self.assertEqual(summary['imports']['count'], 2)
        self.assertEqual(summary['observations']['count'], 2)
        self.assertEqual(history.ingest(self.archive, self.root / 'rebuilt.sqlite'), summary)
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone(), (1,))
            payloads = [json.loads(row[0]) for row in connection.execute(
                'SELECT payload FROM observations ORDER BY observed_at_utc')]
            self.assertEqual(payloads[0]['selections'], payloads[1]['selections'])
            self.assertIsNone(payloads[0]['market']['settlement_rule_id'])
            self.assertEqual(connection.execute('SELECT count(*) FROM analysis_observations').fetchone(), (0,))

    def test_snapshot_evidence_must_be_verified(self):
        text = self.snapshot / 'visible.txt'
        text.write_text(self.visible.replace('+150', '+160'), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'differ from parsed'):
            self.publish()
        text.write_text(self.visible, encoding='utf-8')
        self.metadata['html_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'hash missing or mismatched'):
            self.publish()
        self.assertFalse(self.archive.exists())

    def test_orphans_and_corrupt_batches(self):
        manifest_path = self.publish()
        manifest = json.loads(manifest_path.read_bytes())
        batch = self.archive / history.archive_paths(manifest)[0]
        manifest_path.unlink()
        self.assertEqual(history.ingest(self.archive, self.database)['imports']['count'], 0)
        self.assertEqual(self.publish(), manifest_path)
        original = batch.read_bytes()
        batch.write_bytes(original + b'x')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            history.ingest(self.archive, self.database)
        with self.assertRaisesRegex(ValueError, 'Immutable file conflict'):
            self.publish()
        batch.unlink()
        with self.assertRaises(FileNotFoundError):
            history.ingest(self.archive, self.database)
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM imports').fetchone(), (0,))

    def test_manifest_conflicts_with_import_ledger(self):
        path = self.publish()
        history.ingest(self.archive, self.database)
        manifest = json.loads(path.read_bytes())
        manifest['device'] = 'some-other-device'
        path.write_bytes(history.encode(manifest))
        with self.assertRaisesRegex(ValueError, 'import ledger conflict'):
            history.ingest(self.archive, self.database)

    def test_replacement_preserves_and_links_prior_observation(self):
        first = self.publish()
        parse = history.parse_html

        def next_parser(html, metadata):
            return {**parse(html, metadata), 'parser_version': 3}

        with patch.object(history, 'parse_html', side_effect=next_parser):
            second = self.publish(supersedes=first)
        self.assertNotEqual(second, first)
        self.assertEqual(history.ingest(self.archive, self.database)['observations']['count'], 2)
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute(
                'SELECT count(*) FROM observation_supersessions').fetchone(), (1,))
        first.unlink()
        with self.assertRaisesRegex(ValueError, 'missing predecessor'):
            history.ingest(self.archive, self.root / 'incomplete.sqlite')
        with sqlite3.connect(self.root / 'incomplete.sqlite') as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM observations').fetchone(), (0,))


if __name__ == '__main__':
    unittest.main()
