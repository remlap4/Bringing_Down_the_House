from contextlib import redirect_stdout
from datetime import datetime
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

import history
from moneyline_contract import ContractError
from pinnacle import URL, archive_pinnacle
import run


FIXTURES = Path(__file__).resolve().parent / 'tests' / 'fixtures'


class PinnacleTests(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name).resolve()
        self.snapshot = self.root / 'snapshot'
        self.snapshot.mkdir()
        self.archive = self.root / 'archive'
        self.database = self.root / 'odds.sqlite'
        row = (FIXTURES / 'pinnacle-moneyline-card.html').read_text(encoding='utf-8')
        row = row.replace('<h1>NBA Odds</h1>', '')
        self.html = (
            '<button class="selected-yyBykkcyLl" id="all">All</button>'
            '<section><div data-test-id="Events.DateBar">Today (1)</div>'
            '<div>Handicap Money Line Over Under</div>' + row + '</section>'
        )
        self.text = (FIXTURES / 'pinnacle-moneyline-visible.txt').read_text(encoding='utf-8')
        self.metadata = {
            'final_url': URL + '#all', 'http_status': 200,
            'captured_at_utc': '2026-11-17T22:08:51.093Z',
            'started_at_utc': '2026-11-17T22:08:45Z',
            'browser_timezone': 'America/Toronto',
        }

    def save(self, *, html=None, text=None, **metadata):
        raw = (self.html if html is None else html).encode('utf-8')
        (self.snapshot / 'page.html').write_bytes(raw)
        (self.snapshot / 'visible.txt').write_text(
            self.text if text is None else text, encoding='utf-8')
        (self.snapshot / 'metadata.json').write_text(json.dumps(
            {**self.metadata, 'html_sha256': history.sha(raw), **metadata}), encoding='utf-8')

    def cli(self, *args):
        stdout = io.StringIO()
        with patch('run.Path.cwd', return_value=self.root), patch.dict(run.os.environ), redirect_stdout(stdout):
            code = run.main(['collect', '--sport', 'nba', '--source', 'pinnacle',
                             '--archive', str(self.archive), '--database', str(self.database), *args])
        return code, json.loads(stdout.getvalue())['sources'][0]

    def test_saved_capture_replay_preserves_source_fields(self):
        self.save()
        with patch('pinnacle.PinnacleAdapter.capture') as capture:
            code, report = self.cli('--snapshot', str(self.snapshot))
            capture.assert_not_called()
        self.assertEqual(code, 0)
        self.assertEqual(report['verified_pregame'], 1)
        _, _, records = history.load_batch(self.archive, Path(report['manifest']))
        record, = records
        self.assertEqual(record['event']['source_event_id'], '1640325352')
        self.assertEqual(record['event']['scheduled_start_utc'], '2026-11-18T00:40:00+00:00')
        self.assertEqual(record['observed_at_utc'], '2026-11-17T22:08:51.093000+00:00')
        self.assertIsNone(record['source_updated_at_utc'])
        self.assertEqual([s['odds_text'] for s in record['selections']], ['1.595', '2.480'])
        self.assertEqual([s['source_selection_id'] for s in record['selections']], [None, None])
        self.assertEqual(record['market']['period'], 'full_game')
        self.assertTrue(record['market']['overtime_included'])
        self.assertEqual(record['market']['rule_status'], 'unverified')
        self.assertEqual(record['source_url'], URL)
        history.check_archive(self.archive)
        self.assertTrue(self.database.is_file())

    def test_browser_path_with_mocked_capture(self):
        page = MagicMock(url=URL + '#all')
        page.goto.return_value.status = 200
        page.evaluate.return_value = {
            'html': self.html, 'text': self.text,
            'captured_at_utc': self.metadata['captured_at_utc'],
            'browser_timezone': self.metadata['browser_timezone'],
        }
        browser = MagicMock()
        browser.new_page.return_value = page
        playwright = MagicMock()
        playwright.__enter__.return_value.chromium.launch.return_value = browser
        with (patch('playwright.sync_api.sync_playwright', return_value=playwright),
              patch('source_probe.datetime', wraps=datetime) as clock):
            clock.now.return_value = datetime.fromisoformat(
                '2026-11-17T22:08:45+00:00')
            code, report = self.cli('--headless')
        self.assertEqual((code, report['verified_pregame']), (0, 1))
        page.goto.assert_called_once_with(URL, wait_until='domcontentloaded', timeout=25000)
        browser.close.assert_called_once()

    def test_rejects_bad_capture_without_publishing(self):
        cases = [
            ('tables differ', {'text': self.text.replace('2.480', '1.595')}),
            ('tables differ', {'text': self.text.replace('1.595\n2.480', '2.480\n1.595')}),
            ('Incomplete', {'text': self.text.replace('2.480\n', '')}),
            ('exactly two', {'html': self.html.replace('<button aria-label="Money Line 148"', '<div aria-label="Money Line 148"')}),
            ('Unavailable', {'html': self.html.replace('<button aria-label="Money Line 148"', '<button disabled aria-label="Money Line 148"')}),
            ('NBA All or Game', {'html': self.html.replace('id="all"', 'id="period:1"')}),
            ('Unexpected live', {'html': self.html.replace('data-test-id=""', 'data-mode="live"')}),
            ('disagrees', {'browser_timezone': 'UTC'}),
            ('not after', {'html': self.html.replace('19:40', '16:00'),
                           'text': self.text.replace('19:40', '16:00')}),
            ('HTTP 503', {'http_status': 503}),
            ('hash', {'html_sha256': 'invalid'}),
        ]
        for message, changes in cases:
            with self.subTest(message=message, changes=changes.keys()):
                self.save(**changes)
                with self.assertRaisesRegex(ContractError, message):
                    archive_pinnacle(self.snapshot, self.archive, 'test', 'snapshot/page.html')
                self.assertFalse(self.archive.exists())
