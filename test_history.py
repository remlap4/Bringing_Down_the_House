import csv
import io
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import subprocess
import sys
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

    def test_device_handoff_commands(self):
        # Separate working directories mirror devices with independent SQLite and raw evidence.
        roots = [self.root / device for device in ('device-a', 'device-b')]
        entry = Path(history.__file__).resolve()

        def command(root, *args, succeeds=True):
            result = subprocess.run([sys.executable, str(root / entry.name), *args], cwd=root,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0 if succeeds else 1, result.stderr)
            return result.stdout if succeeds else result.stderr

        archives = []
        for index, root in enumerate(roots):
            snapshot = root / 'data' / 'snapshot'
            snapshot.mkdir(parents=True)
            for module in ('history.py', 'moneyline_contract.py', 'scraper.py', 'verify_snapshot.py'):
                shutil.copyfile(entry.with_name(module), root / module)
            (snapshot / 'page.html').write_text(self.html, encoding='utf-8')
            (snapshot / 'visible.txt').write_text(self.visible, encoding='utf-8')
            metadata = {**self.metadata, 'captured_at_utc': f'2026-11-07T23:0{index}:00Z'}
            (snapshot / 'metadata.json').write_text(json.dumps(metadata), encoding='utf-8')
            command(root, 'archive-snapshot', 'data/snapshot', '--device', root.name)
            self.assertEqual(json.loads(command(root, 'ingest'))['observations']['count'], 1)
            archive = root / 'archive'
            archives.append({path.relative_to(archive): path.read_bytes()
                             for path in archive.rglob('*') if path.is_file()})

        self.assertTrue(archives[0].keys().isdisjoint(archives[1]))
        summaries, exports = [], []
        for root, incoming in zip(roots, reversed(archives)):
            # Transfer only immutable text paths, as a Git merge would.
            for relative, content in incoming.items():
                target = root / 'archive' / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as handle:
                    handle.write(content)
            self.assertEqual(json.loads(command(root, 'check-archive'))['batches'], 2)
            summary = json.loads(command(root, 'ingest'))
            self.assertEqual(json.loads(command(root, 'ingest')), summary)
            self.assertEqual(json.loads(command(root, 'rebuild')), summary)
            self.assertEqual(json.loads(command(root, 'rebuild', '--database', 'data/fresh.sqlite')),
                             summary)
            summaries.append(summary)
            exports.append(command(root, 'export'))
        self.assertEqual(summaries[0], summaries[1])
        self.assertEqual(summaries[0]['imports']['count'], 2)
        self.assertEqual(summaries[0]['observations']['count'], 2)
        self.assertEqual(exports[0], exports[1])
        rows = list(csv.DictReader(io.StringIO(exports[0])))
        self.assertEqual(len(rows), 4)
        self.assertEqual({row['participant'] for row in rows}, {'OKC Thunder', 'CLE Cavaliers'})
        self.assertEqual({row['odds_text'] for row in rows}, {'+150', '−179'})
        self.assertEqual({row['rule_status'] for row in rows}, {'unverified'})

        for relative in archives[1]:
            (roots[0] / 'archive' / relative).unlink()
        self.assertEqual(json.loads(command(roots[0], 'ingest'))['observations']['count'], 2)
        self.assertEqual(json.loads(command(roots[0], 'rebuild'))['observations']['count'], 1)

        self.assertIn('unable to open database', command(roots[0], 'export', '--database',
                                                         'missing.sqlite', succeeds=False))
        self.assertFalse((roots[0] / 'missing.sqlite').exists())

    def test_failed_rebuild_keeps_existing_history(self):
        path = self.publish()
        original = history.ingest(self.archive, self.database)
        batch = self.archive / history.archive_paths(json.loads(path.read_bytes()))[0]
        content = batch.read_bytes()
        for corrupt in (True, False):
            if corrupt:
                batch.write_bytes(content + b'x')
            else:
                batch.unlink()
            with self.assertRaises((ValueError, FileNotFoundError)):
                history.ingest(self.archive, self.database, rebuild=True)
            with sqlite3.connect(self.database) as connection:
                self.assertEqual(history.database_summary(connection), original)

    def test_archive_sharing_checks(self):
        path = self.publish()
        manifest = json.loads(path.read_bytes())
        batch = self.archive / history.archive_paths(manifest)[0]
        size = path.stat().st_size + batch.stat().st_size
        self.assertEqual(history.check_archive(self.archive)['bytes'], size)
        with patch.object(history, 'ARCHIVE_REVIEW_BYTES', size):
            with self.assertRaisesRegex(ValueError, '250 MiB'):
                history.check_archive(self.archive)
        with patch.object(history, 'BATCH_REVIEW_BYTES', batch.stat().st_size):
            with self.assertRaisesRegex(ValueError, '10 MiB'):
                history.check_archive(self.archive)
        manifest['device'] = 'api_key=synthetic-test-value'
        path.write_bytes(history.encode(manifest))
        with self.assertRaisesRegex(ValueError, 'possible credential') as error:
            history.check_archive(self.archive)
        self.assertNotIn('synthetic-test-value', str(error.exception))
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'without a completion manifest'):
            history.check_archive(self.archive)
        self.publish()
        extra = self.archive / 'database.sqlite'
        extra.touch()
        with self.assertRaisesRegex(ValueError, 'Unexpected archive file'):
            history.check_archive(self.archive)

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
