"""Save bounded public-page evidence for source feasibility; does not validate odds."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parent


def capture_page(page, url, folder, timeout_ms):
    folder.mkdir(parents=True, exist_ok=False)
    meta = {'requested_url': url, 'started_at_utc': datetime.now(timezone.utc).isoformat(),
            'status': 'failed', 'errors': [], 'odds_validation': 'not_performed'}
    try:
        response = page.goto(url, wait_until='domcontentloaded', timeout=timeout_ms)
        meta['http_status'] = response.status if response else None
    except Exception as exc:
        meta['errors'].append(f'navigation: {exc}')
    try:
        # Network-idle is only a bounded settling aid, never evidence of complete odds.
        try:
            page.wait_for_load_state('networkidle', timeout=min(timeout_ms, 8000))
        except Exception:
            meta['settle_timeout'] = True
        capture = page.evaluate('''() => ({html: document.documentElement.outerHTML,
            text: document.body.innerText, captured_at_utc: new Date().toISOString(),
            browser_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone})''')
        meta.update({k: v for k, v in capture.items() if k not in ('html', 'text')})
        meta['final_url'] = page.url
        meta['html_sha256'] = hashlib.sha256(capture['html'].encode()).hexdigest()
        (folder / 'page.html').write_text(capture['html'], encoding='utf-8')
        (folder / 'visible.txt').write_text(capture['text'], encoding='utf-8')
        try:
            page.screenshot(path=str(folder / 'page.png'), full_page=True, timeout=timeout_ms)
        except Exception as exc:
            meta['errors'].append(f'screenshot: {exc}')
        if not meta['errors'] and meta.get('http_status') is not None and meta['http_status'] < 400:
            meta['status'] = 'captured_unreviewed'
    except Exception as exc:
        meta['errors'].append(f'capture: {exc}')
    (folder / 'metadata.json').write_text(json.dumps(meta, indent=2) + '\n', encoding='utf-8')
    return meta


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', nargs='+', help='IDs from config/sources.json (including rule-page IDs)')
    parser.add_argument('--output', type=Path, default=ROOT / 'data' / 'source_probes')
    parser.add_argument('--timeout-seconds', type=int, default=25)
    parser.add_argument('--headless', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.timeout_seconds <= 60:
        parser.error('timeout must be between 1 and 60 seconds')
    config = json.loads((ROOT / 'config' / 'sources.json').read_text(encoding='utf-8'))
    urls = {source['id']: source['url'] for source in config['pages']}
    if set(args.source) - urls.keys():
        parser.error('unknown source; inspect config/sources.json')
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / '.browsers'))
    from playwright.sync_api import sync_playwright
    failed = False
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        try:
            for source in args.source:
                stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                folder = args.output / f'{stamp}_{source}_{uuid4().hex[:8]}'
                page = browser.new_page()
                try:
                    meta = capture_page(page, urls[source], folder, args.timeout_seconds * 1000)
                    failed |= meta['status'] == 'failed'
                    print(json.dumps({'source': source, 'folder': str(folder), **meta}), flush=True)
                finally:
                    page.close()
        finally:
            browser.close()
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
