"""Collect and store independently verified sportsbook observations."""
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from history import PublishedBatch, archive_draftkings, ingest
from moneyline_report import report_moneylines
from matching import read_json
from moneyline_contract import fields, require
from pinnacle import PinnacleAdapter
from scraper import capture_snapshot


class Adapter(Protocol):
    source: str

    def capture(self, snapshot: Path) -> None:
        """Save the evidence needed to validate the capture."""

    def archive(self, snapshot: Path, archive: Path, device: str,
                evidence_path: str) -> PublishedBatch:
        """Validate saved evidence and publish a complete normalized batch."""


class DraftKingsAdapter:
    source = 'draftkings'

    def __init__(self, *, headed, channel, via_home):
        self.options = argparse.Namespace(method='browser', headed=headed,
                                          channel=channel, via_home=via_home)

    def capture(self, snapshot):
        capture_snapshot(self.options, snapshot)

    def archive(self, snapshot, archive, device, evidence_path):
        return archive_draftkings(snapshot, archive, device, evidence_path)


def collect(adapters, root, archive, database, device, *, saved_snapshot=None):
    results = []
    for adapter in adapters:
        snapshot = saved_snapshot or root / 'data' / 'raw' / uuid4().hex / adapter.source
        evidence_path = (snapshot / 'page.html').relative_to(root).as_posix()
        result = {'source': adapter.source, 'snapshot': str(snapshot)}
        results.append(result)
        phase = 'capture'
        try:
            if saved_snapshot is None:
                adapter.capture(snapshot)
            phase = 'validate_or_archive'
            batch = adapter.archive(snapshot, archive, device, evidence_path)
            result.update(manifest=str(batch.manifest), observations=batch.observations,
                          verified_pregame=batch.verified_pregame, issues=batch.issues)
            phase = 'import'
            ingest(archive, database, manifest_path=batch.manifest)
        except Exception as exc:
            # Source failures should leave subsequent adapters runnable. Interrupts
            # propagate so a canceled command cannot emit a successful run report.
            result.update(status='failed', phase=phase, error=str(exc) or type(exc).__name__)
        else:
            result['status'] = 'completed'
    return {'database': str(database), 'sources': results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    collect_command = commands.add_parser('collect')
    collect_command.add_argument('--source', choices=['draftkings', 'pinnacle'], default='draftkings')
    collect_command.add_argument('--snapshot', type=Path, help='Replay a saved Pinnacle capture offline')
    moneyline_command = commands.add_parser('moneyline', help='Collect both sources and write an audit CSV')
    moneyline_command.add_argument('--csv', type=Path, required=True, help='Path for a new report CSV')
    moneyline_command.add_argument('--reviews', type=Path, help='JSON with reviewed event mappings and rule bindings')
    for command in (collect_command, moneyline_command):
        command.add_argument('--sport', choices=['nba'], required=True)
        command.add_argument('--device', default='local', help='Non-sensitive capture device label')
        command.add_argument('--archive', type=Path, default=Path('archive'))
        command.add_argument('--database', type=Path, default=Path('data/local/odds.sqlite'))
        command.add_argument('--headless', action='store_true')
        command.add_argument('--channel', choices=['chrome', 'msedge'])
        command.add_argument('--via-home', action='store_true', help='Follow DraftKings’ NBA link from its homepage')
    args = parser.parse_args(argv)
    command = moneyline_command if args.command == 'moneyline' else collect_command
    if not args.device.strip():
        command.error('--device must contain a non-whitespace character')
    root = Path.cwd()
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(Path(__file__).resolve().parent / '.browsers'))
    if args.command == 'moneyline':
        try:
            reviews = read_json(args.reviews) if args.reviews else {'events': [], 'rule_bindings': []}
            fields(reviews, 'events rule_bindings', 'matching reviews')
            require(all(isinstance(value, list) for value in reviews.values()), 'Expected review lists')
            output = args.csv.resolve()
            require(not output.exists(), f'Report path already exists: {output}')
            archive, database = args.archive.resolve(), args.database.resolve()
            require(output != database, 'Report and database paths must differ')
            adapters = [
                PinnacleAdapter(headed=not args.headless, channel=args.channel),
                DraftKingsAdapter(headed=not args.headless, channel=args.channel, via_home=args.via_home),
            ]
            collection = collect(adapters, root, archive, database, args.device)
            return report_moneylines(collection, archive, output, reviews)
        except (ValueError, OSError) as exc:
            print(f'Moneyline report error: {exc}', file=sys.stderr)
            return 1
    snapshot = args.snapshot.resolve() if args.snapshot else None
    if snapshot and (args.source != 'pinnacle' or not snapshot.is_relative_to(root)):
        command.error('--snapshot requires a Pinnacle capture directory under the repository root')
    if args.source == 'pinnacle':
        if args.via_home:
            command.error('--via-home is only supported for DraftKings')
        adapter = PinnacleAdapter(headed=not args.headless, channel=args.channel)
    else:
        adapter = DraftKingsAdapter(headed=not args.headless, channel=args.channel,
                                    via_home=args.via_home)
    report = collect([adapter], root, args.archive.resolve(), args.database.resolve(), args.device,
                     saved_snapshot=snapshot)
    print(json.dumps(report, indent=2))
    return int(any(source['status'] == 'failed' for source in report['sources']))


if __name__ == '__main__':
    raise SystemExit(main())
