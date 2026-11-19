"""One-shot DraftKings NBA HTML capture and offline moneyline parsing."""
import argparse
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

URL = 'https://sportsbook.draftkings.com/leagues/basketball/nba'


def scheduled_start(text, metadata):
    """Resolve displayed dates only when the browser timezone was recorded."""
    if not text or not metadata.get('browser_timezone'):
        return None
    try:
        zone = ZoneInfo(metadata['browser_timezone'])
        captured = datetime.fromisoformat(metadata['captured_at_utc'].replace('Z', '+00:00'))
        if captured.tzinfo is None:
            return None
        local = captured.astimezone(zone)
        match = re.fullmatch(r'(Today|Tomorrow)\s+(\d{1,2}:\d{2}\s*[AP]M)', text, re.I)
        if match:
            day = local.date() + timedelta(days=match[1].lower() == 'tomorrow')
            clock_text = match[2]
        else:
            dated = re.fullmatch(r'(Mon|Tue|Wed|Thu|Fri|Sat|Sun) ([A-Z][a-z]{2}) (\d{1,2})(?:st|nd|rd|th)? (\d{1,2}:\d{2}\s*[AP]M)', text)
            if not dated:
                return None
            # Yearless dates are accepted only when weekday and a nearby future date agree.
            days = []
            for year in (local.year, local.year + 1):
                try:
                    day = datetime.strptime(f'{year} {dated[2]} {dated[3]}', '%Y %b %d').date()
                except ValueError:
                    continue
                if 0 <= (day - local.date()).days <= 183 and day.strftime('%a') == dated[1]:
                    days.append(day)
            if len(days) != 1:
                return None
            day, clock_text = days[0], dated[4]
        clock = datetime.strptime(clock_text.upper().replace(' ', ''), '%I:%M%p').time()
        naive = datetime.combine(day, clock)
        candidates = {naive.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
                      for fold in (0, 1)
                      if naive.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
                      .astimezone(zone).replace(tzinfo=None) == naive}
        return next(iter(candidates)).isoformat() if len(candidates) == 1 else None
    except (ValueError, KeyError, ZoneInfoNotFoundError):
        return None


def event_details(card, links, event_id, metadata):
    teams, scores = [], []
    for link in links:
        name = link.select_one('.cb-market__label-inner')
        score = link.select_one('.cb-market__scoreboard-team-score')
        # Plain links support old/minimal fixtures; never flatten an unknown nested label.
        teams.append(name.get_text(' ', strip=True) if name else
                     (link.get_text(strip=True) if not link.find() else ''))
        raw_score = score.get_text(strip=True) if score else ''
        scores.append(int(raw_score) if raw_score.isdigit() else None)
    wrapper = card.find_parent(class_='cb-static-parlay__event-wrapper')
    container = wrapper if wrapper is not None else card
    # A wrapper can contain an entire day's games. Bind the status cell by event URL.
    cells = [cell for cell in container.select('.cb-event-cell__status')
             if any(re.search(rf'/{event_id}(?:[/?#]|$)', a.get('href', ''))
                    for a in cell.select('a[href]'))]
    scope = cells[0] if len(cells) == 1 else None
    start = scope.select_one(f'[data-testid="cb-start-time-{event_id}"]') if scope else None
    label = start.get_text(' ', strip=True) if start else None
    period = scope.select_one('.cb-event-cell__period') if scope else None
    period_text = period.get_text(' ', strip=True) if period else None
    live = scope is not None and scope.select_one('[data-testid="live-badge"]') is not None
    status = 'unknown'
    if period_text and re.search(r'\b(final|ended)\b', period_text, re.I):
        status = 'final'
    elif live or (period_text and re.search(r'quarter|halftime|overtime', period_text, re.I)):
        status = 'live'
    elif label and not any(score is not None for score in scores):
        status = 'pregame'
    return teams, {'scores_in_display_order': scores, 'event_status': status,
                   'status_evidence': period_text or ('live badge' if live else label),
                   'scheduled_start_text': label,
                   'scheduled_start_utc': scheduled_start(label, metadata)}


def pregame_records(records):
    return [r for r in records if r['event_status'] == 'pregame'
            and r['scheduled_start_utc'] is not None
            and datetime.fromisoformat(r['scheduled_start_utc']) > datetime.fromisoformat(r['captured_at_utc'].replace('Z', '+00:00'))
            and all(s['status'] == 'available' for s in r['selections'])]


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
        teams, details = event_details(card, links, event_id, metadata)
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
                  **details, 'source_updated_at': None,
                  'captured_at_utc': metadata['captured_at_utc'],
                  'source_url': metadata['final_url'], 'html_sha256': metadata.get('html_sha256'),
                  'region_evidence': 'CA-ON' if 'x-pe-loc=CA-ON' in str(card) else None,
                  'selections': selections}
        if event_id in seen:
            if seen[event_id] is not None and seen[event_id] != record:
                issues.append(f'{event_id}: conflicting duplicate quotes; event excluded')
                seen[event_id] = None
            continue
        seen[event_id] = record
    records = [r for r in seen.values() if r is not None]
    if not records:
        raise ValueError('No validated NBA moneyline cards. Capture needs rendering or layout has changed. ' + '; '.join(issues[:3]))
    return {'records': records, 'issues': issues, 'event_count': len(records),
            'parser_version': 2, 'scope': 'observed HTML status and displayed schedule; settlement not verified',
            'status_counts': {s: sum(r['event_status'] == s for r in records)
                              for s in ('pregame', 'live', 'final', 'unknown')},
            'pregame_eligible_count': len(pregame_records(records))}


def save_snapshot(folder, html, metadata):
    folder.mkdir(parents=True, exist_ok=False)
    metadata['html_sha256'] = hashlib.sha256(html.encode('utf-8')).hexdigest()
    (folder / 'page.html').write_text(html, encoding='utf-8')
    (folder / 'metadata.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')


def capture_snapshot(args, folder):
    """Save source evidence before parsing or publishing any observations."""
    started = utc_now()
    status_scope = 'target_navigation'
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
                    if response is not None and response.status >= 400:
                        status_scope = 'homepage_navigation'
                    else:
                        with page.expect_navigation(url=URL, wait_until='domcontentloaded',
                                                    timeout=30000) as navigation:
                            page.locator('a[href="/leagues/basketball/nba"]').first.click(timeout=30000)
                        response = navigation.value
                else:
                    response = page.goto(URL, wait_until='domcontentloaded', timeout=45000)
                if response is not None and response.status < 400:
                    try:
                        page.locator('button[data-testid*="-0ML"]').first.wait_for(state='attached', timeout=30000)
                    except PlaywrightTimeout:
                        pass  # Save the failure HTML too, then let validation report it.
                # Freeze DOM and rendered text together to avoid odds ticking between reads.
                captured = page.evaluate('''() => ({html: document.documentElement.outerHTML,
                    text: document.body.innerText, captured_at: new Date().toISOString(),
                    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone})''')
                html, final_url = captured['html'], page.url
                status = response.status if response else None
                visible_text = captured['text']
                screenshot = page.screenshot(full_page=True)
            finally:
                browser.close()
    metadata = {'requested_url': URL, 'final_url': final_url, 'http_status': status,
                'http_status_scope': status_scope,
                'browser_channel': args.channel if args.method == 'browser' else None,
                'headed': args.headed if args.method == 'browser' else None,
                'via_home': args.via_home if args.method == 'browser' else None,
                'capture_started_at_utc': started,
                'captured_at_utc': captured['captured_at'] if args.method == 'browser' else utc_now(),
                'browser_timezone': captured['timezone'] if args.method == 'browser' else None,
                'method': args.method,
                'region_requested': None, 'note': 'No location override; capture time is not source update time.'}
    save_snapshot(folder, html, metadata)
    if args.method == 'browser':
        (folder / 'visible.txt').write_text(visible_text, encoding='utf-8')
        (folder / 'page.png').write_bytes(screenshot)
    return metadata


def capture(args):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder = Path(args.output) / stamp
    metadata = capture_snapshot(args, folder)
    print(f'Saved HTML and metadata: {folder}')
    status = metadata['http_status']
    if status is not None and status >= 400:
        (folder / 'report.json').write_text(json.dumps({'status':'capture_failed', 'http_status':status,
            'error':'Source returned an HTTP error; snapshot retained, no odds emitted.'}, indent=2), encoding='utf-8')
        raise ValueError(f'Source returned HTTP {status}; inspect saved snapshot. No retry attempted.')
    return process(folder)


def process(folder):
    folder = Path(folder)
    # An unsuccessful reparse must never leave previously valid-looking output behind.
    for name in ('quotes.jsonl', 'pregame_quotes.jsonl', 'verification.json'):
        (folder / name).unlink(missing_ok=True)
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
    result['verification_status'] = 'unverified'
    result['verified_pregame_count'] = 0
    result['missing_start_time_count'] = sum(r['scheduled_start_utc'] is None for r in result['records'])
    from verify_snapshot import verify
    if (folder / 'visible.txt').exists():
        try:
            verify(folder)
        except ValueError as exc:
            result['verification_status'] = 'failed'
            result['issues'].append(f'Rendered-text verification failed: {exc}')
        else:
            result['verification_status'] = 'passed'
            result['verified_pregame_count'] = len(pregame_records(result['records']))
            (folder / 'pregame_quotes.jsonl').write_text(
                ''.join(json.dumps(r) + '\n' for r in pregame_records(result['records'])), encoding='utf-8')
    else:
        result['issues'].append('No rendered text: verified pregame output withheld')
    (folder / 'report.json').write_text(json.dumps({k:v for k,v in result.items() if k != 'records'}, indent=2), encoding='utf-8')
    print(f"Parsed {result['event_count']} events; {result['verified_pregame_count']} verified pregame; "
          f"{len(result['issues'])} issues. See report.json.")
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
