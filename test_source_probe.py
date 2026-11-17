from pathlib import Path
import tempfile
import unittest

from source_probe import capture_page


class FakePage:
    url = 'https://example.com/nba'
    def __init__(self, status=200, navigation_error=False):
        self.status = status
        self.navigation_error = navigation_error
    def goto(self, *args, **kwargs):
        if self.navigation_error: raise TimeoutError('navigation timed out')
        return self
    def wait_for_load_state(self, *args, **kwargs):
        pass
    def evaluate(self, *args):
        return {'html':'<body>Access challenge</body>', 'text':'Access challenge',
                'captured_at_utc':'2026-11-17T20:00:00Z', 'browser_timezone':'America/Toronto'}
    def screenshot(self, path, **kwargs):
        Path(path).write_bytes(b'fixture-not-an-image')


class SourceProbeTests(unittest.TestCase):
    def test_http_200_is_capture_not_odds_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'probe'
            result = capture_page(FakePage(), FakePage.url, folder, 1000)
            self.assertEqual(result['status'], 'captured_unreviewed')
            self.assertEqual(result['odds_validation'], 'not_performed')
            self.assertTrue((folder / 'metadata.json').exists())

    def test_http_failure_retains_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'probe'
            result = capture_page(FakePage(status=403), FakePage.url, folder, 1000)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['http_status'], 403)
            self.assertTrue((folder / 'visible.txt').exists())

    def test_navigation_timeout_is_not_misreported_as_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = capture_page(FakePage(navigation_error=True), FakePage.url, Path(tmp)/'probe', 1000)
            self.assertEqual(result['status'], 'failed')
            self.assertIn('navigation timed out', result['errors'][0])


if __name__ == '__main__':
    unittest.main()
