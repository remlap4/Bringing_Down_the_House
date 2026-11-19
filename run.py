"""Collect and store independently verified sportsbook observations."""
import argparse
import json
import os
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from history import PublishedBatch, archive_draftkings, ingest
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


def collect(adapters, root, archive, database, device):
    results = []
    for adapter in adapters:
        snapshot = root / 'data' / 'raw' / uuid4().hex / adapter.source
        evidence_path = (snapshot / 'page.html').relative_to(root).as_posix()
        result = {'source': adapter.source, 'snapshot': str(snapshot)}
        results.append(result)
        phase = 'capture'
        try:
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
    command = commands.add_parser('collect')
    command.add_argument('--sport', choices=['nba'], required=True)
    command.add_argument('--device', default='local', help='Non-sensitive capture device label')
    command.add_argument('--archive', type=Path, default=Path('archive'))
    command.add_argument('--database', type=Path, default=Path('data/local/odds.sqlite'))
    command.add_argument('--headless', action='store_true')
    command.add_argument('--channel', choices=['chrome', 'msedge'])
    command.add_argument('--via-home', action='store_true', help='Follow DraftKings’ NBA link from its homepage')
    args = parser.parse_args(argv)
    if not args.device.strip():
        command.error('--device must contain a non-whitespace character')
    root = Path.cwd()
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(Path(__file__).resolve().parent / '.browsers'))
    adapters = [DraftKingsAdapter(headed=not args.headless, channel=args.channel,
                                 via_home=args.via_home)]
    report = collect(adapters, root, args.archive.resolve(), args.database.resolve(), args.device)
    print(json.dumps(report, indent=2))
    return int(any(source['status'] == 'failed' for source in report['sources']))


if __name__ == '__main__':
    raise SystemExit(main())
