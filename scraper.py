"""One-shot DraftKings NBA HTML capture and offline moneyline parsing."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

URL = 'https://sportsbook.draftkings.com/leagues/basketball/nba'


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def decimal_odds(text):
    value = text.strip().replace('\u2212', '-').replace('\u00a0', '')
    if value.upper() in ('EVEN', 'EVS'):
        return 2.0
    if not re.fullmatch(r'[+-]\d+', value):
        raise ValueError(f'Unrecognized American odds: {text!r}')
    odds = int(value)
    if abs(odds) < 100:
        raise ValueError(f'Invalid American odds: {text!r}')
    return 1 + (odds / 100 if odds > 0 else 100 / abs(odds))


def parse_html(html, metadata):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')
    heading = soup.find('h1')
    if not heading or 'NBA' not in heading.get_text():
        raise ValueError('NBA heading missing: wrong page, access challenge or incomplete capture.')
    cards = soup.select('[data-testid="market-template"]')
    records, issues, seen = [], [], {}
    for card in cards:
        links = card.select('.cb-side-column__left a[href*="/event/"]')
        if not links:
            continue
        hrefs = {a.get('href') for a in links}
        if len(links) != 2 or len(hrefs) != 1:
            issues.append('Event card has ambiguous team links')
            continue
        href = links[0]['href']
        match = re.search(r'/event/[^/]+/(\d+)(?:[/?#]|$)', href)
        if not match:
            issues.append(f'Unrecognized event URL: {href}')
            continue
        event_id = match.group(1)
        teams = [a.get_text(' ', strip=True) for a in links]
        buttons = card.select('button[data-testid*="-0ML"]')
        if not buttons:
            continue  # Other market templates on the same page.
        header = card.find_parent(class_='cb-static-parlay__wrapper')
        labels = [x.get_text(' ', strip=True) for x in header.select('[data-testid="static-parlay-header-title"] [data-testid="market-label"]')] if header else []
        if 'Moneyline' not in labels or len(buttons) != 2 or len(set(teams)) != 2 or not all(teams):
            issues.append(f'{event_id}: moneyline header, teams or two selections missing')
            continue
        if any(not re.match(rf'component-builder-market-button-{event_id}-0ML', b.get('data-testid', '')) for b in buttons):
            issues.append(f'{event_id}: button belongs to a different event')
            continue
        selections = []
        for team, button in zip(teams, buttons):
            price = button.select_one('[data-testid="button-odds-market-board"]')
            raw = price.get_text(strip=True) if price else None
            unavailable = button.has_attr('disabled') or button.get('aria-disabled') == 'true' or not raw
            try:
                decimal = None if unavailable else decimal_odds(raw)
            except ValueError as exc:
                issues.append(f'{event_id}: {exc}')
                selections = []
                break
            selections.append({'team': team, 'odds_text': raw, 'decimal_odds': decimal,
                               'status': 'unavailable' if unavailable else 'available',
                               'selection_id': button.get('data-testid')})
        if len(selections) != 2:
            continue
        record = {'sportsbook': 'draftkings', 'league': 'NBA', 'event_id': event_id,
                  'event_url': urljoin(metadata['final_url'], href), 'teams_in_display_order': teams,
                  'market': 'moneyline', 'period': 'game_lines_unverified_settlement',
                  'scheduled_start_utc': None, 'source_updated_at': None,
                  'captured_at_utc': metadata['captured_at_utc'],
                  'source_url': metadata['final_url'], 'html_sha256': metadata.get('html_sha256'),
                  'region_evidence': 'CA-ON' if 'x-pe-loc=CA-ON' in str(card) else None,
                  'selections': selections}
        if event_id in seen:
            if seen[event_id] is not None and seen[event_id]['selections'] != selections:
                issues.append(f'{event_id}: conflicting duplicate quotes; event excluded')
                seen[event_id] = None
            continue
        seen[event_id] = record
    records = [r for r in seen.values() if r is not None]
    if not records:
        raise ValueError('No validated NBA moneyline cards. Capture needs rendering or layout has changed. ' + '; '.join(issues[:3]))
    return {'records': records, 'issues': issues, 'event_count': len(records),
            'parser_version': 1, 'scope': 'observed HTML only; schedule, pre-game status and settlement not verified'}


def save_snapshot(folder, html, metadata):
    folder.mkdir(parents=True, exist_ok=False)
    metadata['html_sha256'] = hashlib.sha256(html.encode('utf-8')).hexdigest()
    (folder / 'page.html').write_text(html, encoding='utf-8')
    (folder / 'metadata.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')


def capture(args):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder = Path(args.output) / stamp
    started = utc_now()
    if args.method == 'http':
        request = Request(URL, headers={'User-Agent': 'NBAResearchCollector/0.1', 'Accept': 'text/html'})
        with urlopen(request, timeout=30) as response:
            final_url, status = response.url, response.status
            html = response.read(15_000_001)
            if len(html) > 15_000_000:
                raise ValueError('HTML exceeds 15 MB capture limit')
            html = html.decode(response.headers.get_content_charset() or 'utf-8', errors='replace')
    else:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
        with sync_playwright() as p:
            browser = p.chromium.launch(channel=args.channel, headless=not args.headed)
            try:
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                if args.via_home:
                    response = page.goto('https://sportsbook.draftkings.com/', wait_until='domcontentloaded', timeout=45000)
                    page.locator('a[href="/leagues/basketball/nba"]').first.click(timeout=30000)
                    page.wait_for_url(URL, timeout=30000)
                else:
                    response = page.goto(URL, wait_until='domcontentloaded', timeout=45000)
                if response is not None and response.status < 400:
                    try:
                        page.locator('button[data-testid*="-0ML"]').first.wait_for(state='attached', timeout=30000)
                    except PlaywrightTimeout:
                        pass  # Save the failure HTML too, then let validation report it.
                html, final_url = page.content(), page.url
                status = response.status if response else None
                visible_text = page.locator('body').inner_text()
                screenshot = page.screenshot(full_page=True)
            finally:
                browser.close()
    metadata = {'requested_url': URL, 'final_url': final_url, 'http_status': status,
                'http_status_scope': 'homepage_navigation' if args.method == 'browser' and args.via_home else 'target_navigation',
                'browser_channel': args.channel if args.method == 'browser' else None,
                'headed': args.headed if args.method == 'browser' else None,
                'via_home': args.via_home if args.method == 'browser' else None,
                'capture_started_at_utc': started, 'captured_at_utc': utc_now(), 'method': args.method,
                'region_requested': None, 'note': 'No location override; capture time is not source update time.'}
    save_snapshot(folder, html, metadata)
    if args.method == 'browser':
        (folder / 'visible.txt').write_text(visible_text, encoding='utf-8')
        (folder / 'page.png').write_bytes(screenshot)
    print(f'Saved HTML and metadata: {folder}')
    if status is not None and status >= 400:
        (folder / 'report.json').write_text(json.dumps({'status':'capture_failed', 'http_status':status,
            'error':'Source returned an HTTP error; snapshot retained, no odds emitted.'}, indent=2), encoding='utf-8')
        raise ValueError(f'Source returned HTTP {status}; inspect saved snapshot. No retry attempted.')
    return process(folder)


def process(folder):
    folder = Path(folder)
    metadata = json.loads((folder / 'metadata.json').read_text(encoding='utf-8'))
    if urlparse(metadata['final_url']).hostname != 'sportsbook.draftkings.com':
        raise ValueError('Unexpected redirect host; inspect saved HTML before proceeding.')
    html = (folder / 'page.html').read_text(encoding='utf-8')
    digest = hashlib.sha256(html.encode('utf-8')).hexdigest()
    if metadata.get('html_sha256') and metadata['html_sha256'] != digest:
        raise ValueError('HTML hash mismatch: snapshot has changed.')
    try:
        result = parse_html(html, metadata)
    except ValueError as exc:
        (folder / 'report.json').write_text(json.dumps({'status':'parse_failed', 'error':str(exc)}, indent=2), encoding='utf-8')
        raise
    (folder / 'quotes.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in result['records']), encoding='utf-8')
    (folder / 'report.json').write_text(json.dumps({k:v for k,v in result.items() if k != 'records'}, indent=2), encoding='utf-8')
    print(f"Parsed {result['event_count']} events; {len(result['issues'])} issues. See report.json.")
    return 2 if result['issues'] else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    cap = sub.add_parser('capture')
    cap.add_argument('--method', choices=['http', 'browser'], default='http')
    cap.add_argument('--headed', action='store_true')
    cap.add_argument('--channel', choices=['msedge', 'chrome'], default=None)
    cap.add_argument('--via-home', action='store_true', help='Navigate from the homepage using its NBA link')
    cap.add_argument('--output', default='data')
    offline = sub.add_parser('parse')
    offline.add_argument('snapshot')
    args = parser.parse_args()
    try:
        return capture(args) if args.command == 'capture' else process(args.snapshot)
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
