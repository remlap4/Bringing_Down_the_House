"""Archive verified DraftKings snapshots and ingest normalized history offline."""
import argparse
import csv
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from tempfile import NamedTemporaryFile
from urllib.parse import urlparse

from moneyline_contract import fields, require, string, timestamp, validate_observation
from scraper import parse_html
from verify_snapshot import verify_records


# Each migration and its version marker commit together under the writer lock.
MIGRATIONS = (
    (
        """CREATE TABLE imports (
            batch_id TEXT PRIMARY KEY, manifest_sha256 TEXT NOT NULL,
            batch_sha256 TEXT NOT NULL, row_count INTEGER NOT NULL,
            run_id TEXT NOT NULL,
            supersedes_batch_id TEXT REFERENCES imports(batch_id) DEFERRABLE INITIALLY DEFERRED
        )""",
        """CREATE TABLE observations (
            observation_id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL REFERENCES imports(batch_id),
            observed_at_utc TEXT NOT NULL, source TEXT NOT NULL,
            source_event_id TEXT NOT NULL, rule_status TEXT NOT NULL,
            payload TEXT NOT NULL
        )""",
        """CREATE INDEX observations_event_time
           ON observations(source, source_event_id, observed_at_utc)""",
        """CREATE VIEW observation_supersessions AS
           SELECT new.observation_id, old.observation_id AS supersedes_observation_id
           FROM imports AS batch
           JOIN observations AS new ON new.batch_id = batch.batch_id
           JOIN observations AS old ON old.batch_id = batch.supersedes_batch_id
               AND old.source_event_id = new.source_event_id""",
        """CREATE VIEW analysis_observations AS
           SELECT * FROM observations WHERE rule_status = 'reviewed'
           AND observation_id NOT IN (SELECT supersedes_observation_id FROM observation_supersessions)""",
    ),
)


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                       allow_nan=False) + '\n').encode('utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def identifier(*values):
    return sha(encode(values))


def observation_id(record):
    return identifier(record['source'], record['provenance']['html_sha256'],
                      record['event']['source_event_id'], record['market']['type'],
                      timestamp(record['observed_at_utc'], 'observation time').isoformat(),
                      record['provenance']['parser_version'], record['schema_version'])


def atomic_publish(path, data):
    """Link a fully written file into place without overwriting another writer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, prefix='.', suffix='.tmp', delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            try:
                os.link(temporary, path)
            except FileExistsError:
                require(path.read_bytes() == data, f'Immutable file conflict: {path}')
        finally:
            temporary.unlink(missing_ok=True)


def archive_paths(manifest):
    date = timestamp(manifest['observed_at_utc'], 'manifest observation time').strftime('%Y/%m/%d')
    source, batch_id = manifest['source'], manifest['batch_id']
    return (Path(f'odds/{date}/{source}/{batch_id}.jsonl'),
            Path(f'runs/{date}/{batch_id}.json'))


def normalize(record, run_id, parser_version, captured, started, verification, evidence_path):
    observation = {
        'schema_version': 1, 'observation_id': 'pending', 'run_id': run_id,
        'source': 'draftkings', 'bookmaker': record['sportsbook'],
        'source_url': record['source_url'],
        'observed_at_utc': captured, 'source_updated_at_utc': None,
        'event': {
            'league': record['league'], 'source_event_id': record['event_id'],
            'canonical_event_id': None, 'season': None,
            'participants': [{'source_name': name, 'canonical_team_id': None, 'role': 'unknown'}
                             for name in record['teams_in_display_order']],
            'scheduled_start_utc': record['scheduled_start_utc'],
            'scheduled_start_text': record['scheduled_start_text'],
            'status': record['event_status'], 'status_evidence': record['status_evidence'],
        },
        'market': {'type': 'moneyline', 'period': 'unknown', 'line': None,
                   'overtime_included': None, 'settlement_rule_id': None,
                   'rule_status': 'unverified', 'source_label': 'Moneyline'},
        'selections': [
            {'source_selection_id': selection['selection_id'], 'participant_index': index,
             'odds_text': selection['odds_text'], 'odds_format': 'american',
             'decimal_odds': selection['decimal_odds'], 'status': selection['status']}
            for index, selection in enumerate(record['selections'])
        ],
        'provenance': {
            'parser_version': parser_version, 'html_sha256': record['html_sha256'],
            'capture_started_at_utc': started, 'region': record['region_evidence'],
            'evidence_path': evidence_path,
        },
        'verification': {'price_check': 'passed', 'method': verification},
    }
    observation['observation_id'] = observation_id(observation)
    return validate_observation(observation)


@dataclass(frozen=True)
class PublishedBatch:
    manifest: Path
    observations: int
    verified_pregame: int
    issues: list[str]


def archive_snapshot(snapshot, archive, device, evidence_path, supersedes=None):
    return archive_draftkings(snapshot, archive, device, evidence_path, supersedes).manifest


def archive_draftkings(snapshot, archive, device, evidence_path, supersedes=None):
    string(device, 'device label')
    metadata = json.loads((snapshot / 'metadata.json').read_text(encoding='utf-8'))
    require(isinstance(metadata, dict), 'Expected snapshot metadata object')
    source_url = metadata.get('final_url')
    string(source_url, 'snapshot final URL')
    require(urlparse(source_url).hostname == 'sportsbook.draftkings.com',
            'Expected a DraftKings snapshot')
    status = metadata.get('http_status')
    require(status is None or type(status) is int and 200 <= status < 400,
            f'Snapshot has an unsuccessful HTTP status ({status})')
    html = (snapshot / 'page.html').read_text(encoding='utf-8')
    digest = sha(html.encode('utf-8'))
    require(metadata.get('html_sha256') == digest, 'Snapshot HTML hash missing or mismatched')
    captured = timestamp(metadata.get('captured_at_utc'), 'snapshot capture time').isoformat()
    started = timestamp(metadata.get('capture_started_at_utc'), 'snapshot capture start').isoformat()
    # Reparse raw evidence and independently verify prices without touching derived files.
    parsed = parse_html(html, metadata)
    verified = verify_records(parsed['records'], (snapshot / 'visible.txt').read_text(encoding='utf-8'))
    parser_version = f"draftkings-{parsed['parser_version']}"
    run_id = identifier('draftkings', digest, captured)
    records = [normalize(record, run_id, parser_version, captured, started, verified['method'],
                         evidence_path) for record in parsed['records']]
    return publish_verified_batch(
        archive, device, records, parsed['pregame_eligible_count'], parsed['issues'],
        supersedes=supersedes,
    )


def publish_verified_batch(archive, device, records, verified_pregame, issues, *, supersedes=None):
    string(device, 'device label')
    require(bool(records), 'No verified observations to publish')
    first = records[0]
    source, run_id, captured = (first[key] for key in ('source', 'run_id', 'observed_at_utc'))
    provenance = first['provenance']
    parser_version, started, digest = (
        provenance[key] for key in ('parser_version', 'capture_started_at_utc', 'html_sha256')
    )
    records = sorted(records, key=lambda record: record['observation_id'])
    batch = b''.join(map(encode, records))
    manifest = {
        'manifest_version': 1, 'schema_version': 1, 'status': 'complete',
        'batch_id': identifier(run_id, parser_version, 1), 'run_id': run_id,
        'source': source, 'device': device, 'observed_at_utc': captured,
        'capture_started_at_utc': started, 'parser_version': parser_version,
        'html_sha256': digest, 'batch_sha256': sha(batch), 'row_count': len(records),
        'supersedes_batch_id': None,
    }
    if supersedes is not None:
        previous, _, _ = load_batch(archive, supersedes)
        require(previous['run_id'] == run_id and previous['batch_id'] != manifest['batch_id'],
                'A replacement requires a different parser version for the same capture')
        manifest['supersedes_batch_id'] = previous['batch_id']
    batch_path, manifest_path = archive_paths(manifest)
    # A manifest is the completion marker. Discovery ignores orphaned batch files.
    atomic_publish(archive / batch_path, batch)
    atomic_publish(archive / manifest_path, encode(manifest))
    return PublishedBatch(archive / manifest_path, len(records),
                          verified_pregame, issues)


def load_batch(archive, path):
    manifest_bytes = path.read_bytes()
    manifest = json.loads(manifest_bytes)
    fields(manifest, 'manifest_version schema_version status batch_id run_id source device '
           'observed_at_utc capture_started_at_utc parser_version html_sha256 batch_sha256 row_count '
           'supersedes_batch_id',
           'manifest')
    require(type(manifest['manifest_version']) is int and manifest['manifest_version'] == 1
            and type(manifest['schema_version']) is int and manifest['schema_version'] == 1
            and manifest['status'] == 'complete', f'{path}: unsupported or incomplete manifest')
    require(type(manifest['row_count']) is int and manifest['row_count'] > 0, f'{path}: invalid count')
    for key in ('batch_id', 'run_id', 'html_sha256', 'batch_sha256'):
        require(isinstance(manifest[key], str) and re.fullmatch(r'[0-9a-f]{64}', manifest[key]),
                f'{path}: invalid {key}')
    previous = manifest['supersedes_batch_id']
    require(previous is None or isinstance(previous, str) and re.fullmatch(r'[0-9a-f]{64}', previous)
            and previous != manifest['batch_id'], f'{path}: invalid predecessor')
    for key in ('device', 'parser_version'):
        string(manifest[key], key)
    require(isinstance(manifest['source'], str) and re.fullmatch(r'[a-z0-9_-]+', manifest['source']),
            f'{path}: invalid source')
    require(manifest['batch_id'] == identifier(manifest['run_id'], manifest['parser_version'],
                                               manifest['schema_version']), f'{path}: invalid batch ID')
    captured = timestamp(manifest['observed_at_utc'], 'manifest observation time').isoformat()
    require(manifest['observed_at_utc'] == captured, f'{path}: noncanonical observation time')
    require(manifest['run_id'] == identifier(manifest['source'], manifest['html_sha256'], captured),
            f'{path}: invalid run ID')
    batch_path, manifest_path = archive_paths(manifest)
    require(path == archive / manifest_path, f'{path}: manifest is in the wrong location')
    batch = (archive / batch_path).read_bytes()
    require(sha(batch) == manifest['batch_sha256'], f'{path}: batch checksum mismatch')
    records = [validate_observation(json.loads(line)) for line in batch.split(b'\n')[:-1]]
    require(len(records) == manifest['row_count'] and batch == b''.join(map(encode, records)),
            f'{path}: invalid row count or JSONL encoding')
    seen = set()
    for record in records:
        provenance = record['provenance']
        for key in ('run_id', 'source', 'observed_at_utc', 'schema_version'):
            require(record[key] == manifest[key], f'{path}: inconsistent {key}')
        for key in ('parser_version', 'html_sha256', 'capture_started_at_utc'):
            require(provenance[key] == manifest[key], f'{path}: inconsistent {key}')
        key = record['observation_id']
        require(key == observation_id(record) and key not in seen, f'{path}: invalid or duplicate observation ID')
        seen.add(key)
    return manifest, sha(manifest_bytes), records


def migrate(connection):
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        version = connection.execute('PRAGMA user_version').fetchone()[0]
        require(version <= len(MIGRATIONS), 'Database schema is newer than this importer')
        for number, statements in enumerate(MIGRATIONS[version:], start=version + 1):
            for statement in statements:
                connection.execute(statement)
            connection.execute(f'PRAGMA user_version = {number}')


def database_summary(connection):
    counts = {}
    for table, key in (('imports', 'batch_id'), ('observations', 'observation_id')):
        digest = hashlib.sha256()
        count = 0
        for row in connection.execute(f'SELECT * FROM {table} ORDER BY {key}'):
            digest.update(encode(row))
            count += 1
        counts[table] = {'count': count, 'sha256': digest.hexdigest()}
    return counts


def ingest(archive, database, *, manifest_path=None, rebuild=False):
    if rebuild:
        require(archive.is_dir(), 'Rebuild requires an existing archive directory')
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute('PRAGMA foreign_keys = ON')
        migrate(connection)
        # One transaction permits predecessor batches to appear in any file order.
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            if rebuild:
                # Keep replacement rows and deletion of the old history in the same transaction.
                connection.execute('DELETE FROM observations')
                connection.execute('DELETE FROM imports')
            # A collection imports its own batch even if unrelated archive files need repair.
            paths = ([manifest_path] if manifest_path is not None else
                     sorted((archive / 'runs').glob('*/*/*/*.json')))
            for path in paths:
                manifest, digest, records = load_batch(archive, path)
                batch_id = manifest['batch_id']
                ledger = (batch_id, digest, manifest['batch_sha256'], manifest['row_count'],
                          manifest['run_id'], manifest['supersedes_batch_id'])
                prior = connection.execute('SELECT * FROM imports WHERE batch_id = ?', (batch_id,)).fetchone()
                if prior is not None:
                    require(prior == ledger, f'{path}: import ledger conflict')
                    continue
                connection.execute('INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?)', ledger)
                connection.executemany('INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?)', (
                    (record['observation_id'], batch_id, record['observed_at_utc'], record['source'],
                     record['event']['source_event_id'], record['market']['rule_status'],
                     encode(record).decode('utf-8'))
                    for record in records
                ))
            invalid = connection.execute("""
                WITH RECURSIVE ancestry(batch_id, ancestor_id) AS (
                    SELECT batch_id, supersedes_batch_id FROM imports WHERE supersedes_batch_id IS NOT NULL
                    UNION
                    SELECT ancestry.batch_id, imports.supersedes_batch_id
                    FROM ancestry JOIN imports ON imports.batch_id = ancestry.ancestor_id
                    WHERE imports.supersedes_batch_id IS NOT NULL
                )
                SELECT 1 FROM ancestry
                JOIN imports AS child ON child.batch_id = ancestry.batch_id
                LEFT JOIN imports AS parent ON parent.batch_id = ancestry.ancestor_id
                WHERE parent.run_id IS NULL OR child.run_id != parent.run_id
                    OR child.batch_id = parent.batch_id
                LIMIT 1
            """).fetchone()
            require(invalid is None, 'Invalid batch ancestry: missing predecessor, different capture or cycle')
            summary = database_summary(connection)
        return summary



# Conservative review thresholds for Git transport, independent of local ingestion.
BATCH_REVIEW_BYTES = 10 * 1024**2
ARCHIVE_REVIEW_BYTES = 250 * 1024**2
SECRET_PATTERN = re.compile(
    r'-----BEGIN [A-Z ]*PRIVATE KEY-----'
    r'|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'
    r'|\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b'
    r'|\bBearer\s+[A-Za-z0-9._~+/-]{8,}'
    r'|\b(?:api[_-]?key|(?:access|refresh|auth|session)[_-]?token|token|'
    r'password|secret|client[_-]?secret|cookie|authorization)\s*[:=]\s*\S',
    re.IGNORECASE,
)


def check_archive(archive):
    """Check the complete shareable tree before selecting files for Git."""
    require(archive.is_dir() and not archive.is_symlink(), 'Expected a regular archive directory')
    files = set()
    manifests = []
    total = 0
    largest = 0
    for path in sorted(archive.rglob('*')):
        relative = path.relative_to(archive).as_posix()
        require(not path.is_symlink(), f'Unexpected archive symlink: {relative}')
        if path.is_dir():
            continue
        manifest = re.fullmatch(r'runs/\d{4}/\d{2}/\d{2}/[0-9a-f]{64}\.json', relative)
        batch = re.fullmatch(r'odds/\d{4}/\d{2}/\d{2}/[a-z0-9_-]+/[0-9a-f]{64}\.jsonl', relative)
        require(path.is_file() and (manifest or batch), f'Unexpected archive file: {relative}')
        size = path.stat().st_size
        require(size < BATCH_REVIEW_BYTES, f'Archive file reached the 10 MiB review threshold: {relative}')
        total += size
        require(total < ARCHIVE_REVIEW_BYTES, 'Archive reached the 250 MiB review threshold')
        largest = max(largest, size)
        files.add(path)
        if manifest:
            manifests.append(path)

    expected = set()
    for path in manifests:
        manifest, _, records = load_batch(archive, path)
        batch_path, _ = archive_paths(manifest)
        expected.update((path, archive / batch_path))
        # Refuse URL query strings and fragments because arbitrary parameter names can carry credentials.
        for record in records:
            source_url = urlparse(record['source_url'])
            require(not (source_url.query or source_url.fragment),
                    f'{path}: source URL contains query or fragment')
        require(not any(SECRET_PATTERN.search(encode(value).decode('utf-8'))
                        for value in (manifest, *records)),
                f'{path}: possible credential in archive metadata or observations')
    require(files == expected, 'Archive contains a batch without a completion manifest')
    return {'batches': len(manifests), 'bytes': total, 'largest_file_bytes': largest}


def export_csv(database, output):
    """Stream one row per selection in stable observation and participant order."""
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        rows = connection.execute('SELECT batch_id, payload FROM observations ORDER BY observation_id')
        writer = csv.writer(output, lineterminator='\n')
        writer.writerow(('batch_id', 'observation_id', 'observed_at_utc', 'source', 'bookmaker',
                         'source_event_id', 'scheduled_start_utc', 'event_status', 'market_type',
                         'rule_status', 'participant_index', 'participant', 'odds_text',
                         'odds_format', 'decimal_odds', 'selection_status'))
        for batch_id, payload in rows:
            record = json.loads(payload)
            event = record['event']
            market = record['market']
            for selection in sorted(record['selections'], key=lambda item: item['participant_index']):
                index = selection['participant_index']
                writer.writerow((batch_id, record['observation_id'], record['observed_at_utc'],
                                 record['source'], record['bookmaker'], event['source_event_id'],
                                 event['scheduled_start_utc'], event['status'], market['type'],
                                 market['rule_status'], index, event['participants'][index]['source_name'],
                                 selection['odds_text'], selection['odds_format'],
                                 selection['decimal_odds'], selection['status']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, default=Path('archive'))
    commands = parser.add_subparsers(dest='command', required=True)
    snapshot = commands.add_parser('archive-snapshot')
    snapshot.add_argument('snapshot', type=Path)
    snapshot.add_argument('--device', required=True, help='Stable, non-sensitive capture device label')
    snapshot.add_argument('--evidence-path', help='Repository-relative path to the original page.html')
    snapshot.add_argument('--supersedes', type=Path, help='Prior manifest for the same capture')
    commands.add_parser('check-archive', help='Check completeness, size and common credential patterns')
    for command, help_text in (
        ('ingest', 'Import complete batches into local history'),
        ('rebuild', 'Replace local history transactionally from complete batches'),
        ('export', 'Write historical selections as CSV to standard output'),
    ):
        database_command = commands.add_parser(command, help=help_text)
        database_command.add_argument('--database', type=Path, default=Path('data/local/odds.sqlite'))
    args = parser.parse_args()
    try:
        if args.command == 'archive-snapshot':
            evidence_path = args.evidence_path
            if evidence_path is None:
                evidence_path = (args.snapshot.resolve() / 'page.html').relative_to(Path.cwd()).as_posix()
            print(archive_snapshot(args.snapshot, args.archive.resolve(), args.device, evidence_path,
                                   args.supersedes.resolve() if args.supersedes else None))
        elif args.command == 'check-archive':
            print(json.dumps(check_archive(args.archive), sort_keys=True))
        elif args.command == 'export':
            sys.stdout.reconfigure(encoding='utf-8', newline='')
            export_csv(args.database, sys.stdout)
        else:
            print(json.dumps(ingest(args.archive, args.database, rebuild=args.command == 'rebuild'),
                             sort_keys=True))
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f'History error: {exc}\n')


if __name__ == '__main__':
    main()
