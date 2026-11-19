from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

import history
import run
import test_scraper


class CollectionTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.archive = self.root / 'archive'
        self.database = self.root / 'data/local/odds.sqlite'
        event = test_scraper.EventTests()
        self.page = MagicMock()
        self.page.goto.return_value.status = 200
        self.page.url = test_scraper.URL
        self.page.evaluate.return_value = {
            'html': event.fixture(event.start()),
            'text': ('OKC Thunder\nAT\nCLE Cavaliers\n+100\n+100\n+150\n'
                     '+100\n+100\n−179\nToday 7:40 PM\nMore Bets'),
            'captured_at': event.meta['captured_at_utc'],
            'timezone': event.meta['browser_timezone'],
        }
        self.page.screenshot.return_value = b'mocked screenshot'
        browser = MagicMock()
        browser.new_page.return_value = self.page
        playwright = MagicMock()
        playwright.__enter__.return_value.chromium.launch.return_value = browser
        self.patch_for_test('playwright.sync_api.sync_playwright', return_value=playwright)
        self.patch_for_test('scraper.utc_now', return_value='2026-11-07T22:59:00Z')

    def patch_for_test(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def run_cli(self, *options):
        stdout = io.StringIO()
        with (patch('run.Path.cwd', return_value=self.root),
              patch.dict(run.os.environ), redirect_stdout(stdout)):
            code = run.main(['collect', '--sport', 'nba', '--archive', str(self.archive),
                             '--database', str(self.database), '--headless', *options])
        return code, json.loads(stdout.getvalue())['sources'][0]

    def test_collect_validates_publishes_and_imports_own_batch(self):
        broken = self.archive / 'runs/2026/11/06/unrelated.json'
        broken.parent.mkdir(parents=True)
        broken.write_text('{}', encoding='utf-8')
        code, result = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['observations'], 1)
        self.assertEqual(result['verified_pregame'], 1)
        manifest, _, records = history.load_batch(self.archive, Path(result['manifest']))
        self.assertEqual(manifest['row_count'], 1)
        evidence = records[0]['provenance']['evidence_path']
        self.assertEqual(self.root / evidence, Path(result['snapshot']) / 'page.html')
        self.assertTrue((self.root / evidence).is_file())
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute('SELECT batch_id FROM imports').fetchall(),
                             [(manifest['batch_id'],)])

    def test_capture_failure_is_isolated(self):
        failed = MagicMock(source='broken')
        failed.capture.side_effect = TimeoutError('navigation timed out')
        draftkings = run.DraftKingsAdapter(headed=False, channel=None, via_home=False)
        results = run.collect([failed, draftkings], self.root, self.archive, self.database,
                              'test')['sources']
        self.assertEqual([result['status'] for result in results], ['failed', 'completed'])
        self.assertEqual(results[0]['phase'], 'capture')
        self.assertIn('navigation timed out', results[0]['error'])
        failed.archive.assert_not_called()
        self.assertEqual(len(list((self.archive / 'runs').rglob('*.json'))), 1)

    def test_failed_validation_leaves_evidence_but_no_batch(self):
        self.page.evaluate.return_value['text'] = self.page.evaluate.return_value['text'].replace(
            '+150', '+160')
        code, result = self.run_cli()
        self.assertEqual(code, 1)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['phase'], 'validate_or_archive')
        self.assertIn('differ from parsed', result['error'])
        self.assertTrue((Path(result['snapshot']) / 'visible.txt').is_file())
        self.assertFalse(self.archive.exists())
        self.assertFalse(self.database.exists())

    def test_http_failure_with_parseable_evidence_does_not_publish(self):
        self.page.goto.return_value.status = 503
        code, result = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn('503', result['error'])
        self.assertFalse(self.archive.exists())

    def test_via_home_checks_target_response(self):
        navigation = self.page.expect_navigation.return_value.__enter__.return_value
        navigation.value.status = 503
        code, result = self.run_cli('--via-home')
        self.assertEqual(code, 1)
        self.assertIn('503', result['error'])
        self.assertFalse(self.archive.exists())

    def test_interrupt_before_manifest_leaves_only_an_ignored_orphan(self):
        publish = history.atomic_publish

        def interrupt_manifest(path, data):
            if path.suffix == '.json':
                raise KeyboardInterrupt
            publish(path, data)

        with patch.object(history, 'atomic_publish', side_effect=interrupt_manifest):
            with self.assertRaises(KeyboardInterrupt):
                self.run_cli()
        self.assertEqual(len(list(self.archive.rglob('*.jsonl'))), 1)
        self.assertFalse(list(self.archive.rglob('*.json')))
        self.assertFalse(self.database.exists())
        self.assertEqual(history.ingest(self.archive, self.database)['imports']['count'], 0)

    def test_import_failure_reports_the_recoverable_manifest(self):
        with patch('run.ingest', side_effect=sqlite3.OperationalError('database unavailable')):
            code, result = self.run_cli()
        self.assertEqual(code, 1)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['phase'], 'import')
        self.assertTrue(Path(result['manifest']).is_file())
        self.assertEqual(history.ingest(self.archive, self.database)['imports']['count'], 1)


if __name__ == '__main__':
    unittest.main()
