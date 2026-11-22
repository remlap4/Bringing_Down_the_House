"""Validate Pinnacle NBA board captures and archive full-game moneylines."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from history import identifier, observation_id, publish_verified_batch, sha
from moneyline_contract import require, timestamp, url, validate_observation
from source_probe import capture_page

URL = 'https://www.pinnacle.com/en/basketball/nba/matchups/'
PARSER_VERSION = 'pinnacle-1'
EVENT_LINK = re.compile(r'/en/basketball/nba/[^/]+/([0-9]+)/')
DECIMAL = r'[0-9]+\.[0-9]+'
CLOCK = re.compile(r'(\d{2}:\d{2}:\d{2}) \(GMT ([+-]\d{2}:\d{2})\)')
DAY = re.compile(r'(TODAY|TOMORROW)\s*\(([0-9]+)\)', re.IGNORECASE)
COLUMNS = ['HANDICAP', 'MONEY LINE', 'OVER', 'UNDER']


def visible_board(text):
    """Read the board as displayed, using its column order rather than HTML selectors."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    require(lines.count('NBA Odds') == 1 and lines.count('Decimal Odds') == 1,
            'Expected a rendered NBA board in decimal format')
    lines = lines[lines.index('NBA Odds') + 1:]
    boards = []
    index = 0
    while index < len(lines):
        header = DAY.fullmatch(' '.join(lines[index:index + 2]))
        if not header or lines[index + 2:index + 6] != COLUMNS:
            index += 1
            continue
        day, count = header.groups()
        index += 6
        rows = []
        for _ in range(int(count)):
            row = lines[index:index + 14]
            require(len(row) == 14 and re.fullmatch(r'\d{2}:\d{2}', row[2])
                    and all(re.fullmatch(r'[+-][0-9]+(?:\.[0-9]+)?', row[i]) for i in (3, 5))
                    and all(re.fullmatch(DECIMAL, row[i]) for i in (4, 6, 7, 8, 10, 12))
                    and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', row[9])
                    and row[9] == row[11] and re.fullmatch(r'\+[0-9]+', row[13]),
                    'Incomplete or unexpected displayed event row')
            rows.append((tuple(row[:2]), row[2], tuple(row[7:9])))
            index += 14
        boards.append((day.lower(), rows))
    require(bool(boards), 'No supported rendered pregame moneyline table')
    return boards


def parse_board(html):
    soup = BeautifulSoup(html, 'html.parser')
    selected = soup.select('button.selected-yyBykkcyLl')
    require(len(selected) == 1 and selected[0].get('id') in ('all', 'period:0'),
            'Expected the NBA All or Game tab')
    boards = []
    for group in soup.select('[data-test-id="Events.DateBar"]'):
        if group.find_parent(attrs={'data-mode': 'live'}):
            continue
        header = DAY.fullmatch(group.get_text(' ', strip=True))
        require(header is not None, 'Unsupported Pinnacle date group')
        day, count = header.groups()
        rows = []
        # The first sibling contains column labels. Each remaining sibling
        # must account for one event reported by the date-group counter.
        siblings = group.find_next_siblings()
        require(len(siblings) == int(count) + 1 and
                siblings[0].get_text(' ', strip=True).upper().split() ==
                'HANDICAP MONEY LINE OVER UNDER'.split(), 'Unexpected Pinnacle table layout')
        for row in siblings[1:]:
            require(not row.select_one('[data-mode="live"]') and row.get('data-mode') != 'live',
                    'Unexpected live event in the pregame table')
            link = row.select_one('[class^="metadata-"] a[href]')
            match = EVENT_LINK.fullmatch(link['href']) if link else None
            require(match is not None, 'Missing Pinnacle NBA event ID')
            names = tuple(node.get_text(strip=True) for node in
                          link.select('span[class*="gameInfoLabel-"]'))
            start = link.select_one('[class^="matchupDate-"]')
            require(len(names) == 2 and start is not None, 'Missing teams or start time')
            markets = row.select('[data-test-id="moneyline"]')
            require(len(markets) == 1 and markets[0].parent is row, 'Missing or ambiguous moneyline')
            buttons = markets[0].select('button')
            require(len(buttons) == 2, 'Expected exactly two moneyline selections')
            prices = []
            for button in buttons:
                price = button.select_one('[class^="price-"]')
                require(price is not None and not button.has_attr('disabled') and
                        button.get('aria-disabled') != 'true',
                        'Unavailable moneyline selection')
                odds = price.get_text(strip=True)
                require(re.fullmatch(DECIMAL, odds) is not None, 'Invalid decimal moneyline price')
                prices.append(odds)
            rows.append((match[1], (names, start.get_text(strip=True), tuple(prices))))
        boards.append((day.lower(), rows))
    require(bool(boards), 'No supported Pinnacle pregame table in HTML')
    return boards


def local_observation_time(text, observed, zone_name):
    # Use the page clock to corroborate the browser timezone before resolving
    # relative date headings. A stale clock or conflicting offset is ambiguous.
    clocks = [match for line in text.splitlines() if (match := CLOCK.fullmatch(line.strip()))]
    require(len(clocks) == 1 and isinstance(zone_name, str), 'Missing Pinnacle display timezone')
    local = observed.astimezone(ZoneInfo(zone_name))
    clock, offset = clocks[0].groups()
    displayed = datetime.fromisoformat(f'{local.date()}T{clock}{offset}')
    require(displayed.utcoffset() == local.utcoffset() and
            abs(observed - displayed) <= timedelta(minutes=1),
            'Pinnacle display clock disagrees with observation time')
    return local


def scheduled_start(local, day, clock):
    date = local.date() + timedelta(days=(day == 'tomorrow'))
    naive = datetime.strptime(f'{date} {clock}', '%Y-%m-%d %H:%M')
    times = {naive.replace(tzinfo=local.tzinfo, fold=fold).astimezone(timezone.utc)
             for fold in (0, 1)}
    require(len(times) == 1, 'Ambiguous or nonexistent Pinnacle start time')
    start = times.pop()
    require(start > local, 'Pinnacle start is not after observation time')
    return start.isoformat()


def archive_pinnacle(snapshot, archive, device, evidence_path):
    metadata = json.loads((snapshot / 'metadata.json').read_text(encoding='utf-8'))
    require(isinstance(metadata, dict), 'Expected Pinnacle snapshot metadata object')
    source_url = metadata.get('final_url')
    url(source_url, 'snapshot final URL')
    parsed_url = urlparse(source_url)
    require(parsed_url._replace(fragment='').geturl() == URL and
            parsed_url.fragment in ('', 'all', 'period:0'), 'Expected a Pinnacle NBA board URL')
    status = metadata.get('http_status')
    require(type(status) is int and 200 <= status < 300 and
            metadata.get('status') in (None, 'captured_unreviewed') and not metadata.get('errors'),
            f'Unsuccessful Pinnacle capture (HTTP {status})')
    raw = (snapshot / 'page.html').read_bytes()
    digest = sha(raw)
    require(metadata.get('html_sha256') == digest, 'Snapshot HTML hash missing or mismatched')
    observed = timestamp(metadata.get('captured_at_utc'), 'snapshot observation time')
    started = timestamp(metadata.get('started_at_utc'), 'snapshot capture start').isoformat()
    text = (snapshot / 'visible.txt').read_text(encoding='utf-8')
    boards = parse_board(raw.decode('utf-8'))
    require([(day, [values for _, values in rows]) for day, rows in boards] == visible_board(text),
            'Pinnacle HTML and rendered moneyline tables differ')
    local = local_observation_time(text, observed, metadata.get('browser_timezone'))

    # Record the published game rules while leaving per-quote applicability
    # unresolved. History excludes unverified bindings from its analysis view.
    config = Path(__file__).resolve().parent / 'config' / 'settlement_rules.json'
    profile, = [profile for profile in json.loads(config.read_text(encoding='utf-8'))['profiles']
                if profile['id'] == 'pinnacle:nba:full_game:2026-11-17']
    run_id = identifier('pinnacle', digest, observed.isoformat())
    records = []
    seen = set()
    for day, rows in boards:
        for event_id, (names, clock, prices) in rows:
            require(event_id not in seen, f'Duplicate Pinnacle event ID {event_id}')
            seen.add(event_id)
            record = {
                'schema_version': 1, 'observation_id': 'pending', 'run_id': run_id,
                # Keep navigation fragments in raw metadata. Archive URLs are shareable.
                'source': 'pinnacle', 'bookmaker': 'pinnacle', 'source_url': URL,
                'observed_at_utc': observed.isoformat(), 'source_updated_at_utc': None,
                'event': {
                    'league': 'NBA', 'source_event_id': event_id, 'canonical_event_id': None,
                    'season': None, 'participants': [
                        {'source_name': name, 'canonical_team_id': None, 'role': 'unknown'}
                        for name in names],
                    'scheduled_start_utc': scheduled_start(local, day, clock),
                    'scheduled_start_text': f'{day.title()} {clock}', 'status': 'pregame',
                    'status_evidence': 'Pinnacle date group and future displayed start',
                },
                'market': {
                    'type': 'moneyline', 'period': profile['period'], 'line': None,
                    'overtime_included': profile['overtime_included'],
                    'settlement_rule_id': profile['id'], 'rule_status': 'unverified',
                    'source_label': 'Money Line',
                },
                'selections': [
                    {'source_selection_id': None, 'participant_index': index,
                     'odds_text': price, 'odds_format': 'decimal', 'decimal_odds': float(price),
                     'status': 'available'} for index, price in enumerate(prices)],
                'provenance': {'parser_version': PARSER_VERSION, 'html_sha256': digest,
                               'capture_started_at_utc': started, 'region': None,
                               'evidence_path': evidence_path},
                'verification': {'price_check': 'passed', 'method': 'pinnacle_rendered_table_v1'},
            }
            record['observation_id'] = observation_id(record)
            records.append(validate_observation(record))
    return publish_verified_batch(archive, device, records, len(records), [])


class PinnacleAdapter:
    source = 'pinnacle'
    archive = staticmethod(archive_pinnacle)

    def __init__(self, *, headed, channel):
        self.headed = headed
        self.channel = channel

    def capture(self, snapshot):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not self.headed, channel=self.channel)
            try:
                metadata = capture_page(browser.new_page(), URL, snapshot, timeout_ms=25000)
            finally:
                browser.close()
        require(metadata['status'] == 'captured_unreviewed',
                f"Pinnacle capture failed (HTTP {metadata.get('http_status')})")
