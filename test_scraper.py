import unittest
from scraper import parse_html, decimal_odds, URL, scheduled_start, pregame_records, process
from pathlib import Path
import tempfile
import json

# Reduced structural fixture based on observed DraftKings CA-ON HTML.
# This is not an archived full-page capture or a current betting offer.
HTML = '''<h1>NBA Odds</h1><div class="cb-static-parlay__wrapper">
<div data-testid="static-parlay-header-title"><p data-testid="market-label">Spread</p><p data-testid="market-label">Total</p><p data-testid="market-label">Moneyline</p></div>
<div data-testid="market-template"><div class="cb-side-column__left">
<a href="/event/okc-thunder-%40-cle-cavaliers/34936963">OKC Thunder</a>
<a href="/event/okc-thunder-%40-cle-cavaliers/34936963">CLE Cavaliers</a>
</div><div class="cb-side-column__right">
<button data-testid="component-builder-market-button-34936963-0HC87027629P350_3"><span data-testid="button-odds-market-board">−103</span></button>
<button data-testid="component-builder-market-button-34936963-0ML87027629_3"><span data-testid="button-odds-market-board">+150</span></button>
<button data-testid="component-builder-market-button-34936963-0ML87027629_1"><span data-testid="button-odds-market-board">−179</span></button>
</div></div></div>'''
META = {'final_url': URL, 'captured_at_utc':'2026-11-02T19:00:00+00:00'}

class ParserTests(unittest.TestCase):
    def test_moneyline_not_spread(self):
        r = parse_html(HTML, META)['records'][0]
        self.assertEqual(r['event_id'], '34936963')
        self.assertEqual(r['selections'][0]['team'], 'OKC Thunder')
        self.assertEqual(r['selections'][0]['decimal_odds'], 2.5)
        self.assertAlmostEqual(r['selections'][1]['decimal_odds'], 1+100/179)
        self.assertIsNone(r['source_updated_at'])

    def test_unicode_minus_and_even(self):
        self.assertEqual(decimal_odds('−200'), 1.5)
        self.assertEqual(decimal_odds('EVEN'), 2)
        for value in ['0', '+0', '+50', 'LOCK', '2.5']:
            with self.assertRaises(ValueError): decimal_odds(value)

    def test_wrong_page_and_empty(self):
        for html in ['<h1>Access denied</h1>', '<h1>NBA Odds</h1>']:
            with self.assertRaises(ValueError): parse_html(html, META)

    def test_unavailable_quote_preserved(self):
        r = parse_html(HTML.replace('type-never', '').replace('data-testid="component-builder-market-button-34936963-0ML87027629_1"', 'disabled data-testid="component-builder-market-button-34936963-0ML87027629_1"'), META)
        self.assertIsNone(r['records'][0]['selections'][1]['decimal_odds'])

    def test_missing_header_fails(self):
        with self.assertRaises(ValueError): parse_html(HTML.replace('Moneyline', 'First Quarter'), META)

    def test_identical_duplicates_deduplicated(self):
        self.assertEqual(parse_html(HTML+HTML, META)['event_count'], 1)

    def test_conflicting_duplicates_fail(self):
        with self.assertRaises(ValueError): parse_html(HTML+HTML.replace('+150', '+160')+HTML, META)

class EventTests(unittest.TestCase):
    meta = {**META, 'captured_at_utc': '2026-11-07T23:00:00+00:00',
            'browser_timezone': 'America/Toronto'}

    def fixture(self, status='', scores=False):
        html = HTML
        for team, score in [('OKC Thunder', 49), ('CLE Cavaliers', 30)]:
            label = f'<span class="cb-market__label-inner">{team}</span>'
            if scores:
                label += f'<span class="cb-market__scoreboard-team-score">{score}</span>'
            html = html.replace(f'>{team}</a>', f'>{label}</a>')
        # Status is a sibling of the market template inside the event wrapper.
        cell = '<div class="cb-event-cell__status"><a href="/event/test/34936963">More Bets</a>' + status + '</div>'
        return '<div class="cb-static-parlay__event-wrapper">' + html + cell + '</div>'

    def start(self, text='Today 7:40 PM'):
        return f'<span data-testid="cb-start-time-34936963">{text}</span>'

    def record(self, status='', scores=False, meta=None):
        return parse_html(self.fixture(status, scores), meta or self.meta)['records'][0]

    def test_live_scores_not_part_of_team(self):
        r = self.record('<svg data-testid="live-badge"></svg>', True)
        self.assertEqual(r['teams_in_display_order'], ['OKC Thunder', 'CLE Cavaliers'])
        self.assertEqual(r['scores_in_display_order'], [49, 30])
        self.assertEqual(r['event_status'], 'live')
        self.assertEqual(pregame_records([r]), [])

    def test_pregame_utc_and_filter(self):
        r = self.record(self.start())
        self.assertEqual(r['scheduled_start_utc'], '2026-11-08T00:40:00+00:00')
        self.assertEqual(pregame_records([r]), [r])
        r['selections'][0]['status'] = 'unavailable'
        self.assertEqual(pregame_records([r]), [])

    def test_browser_iso_timestamp_on_python_310(self):
        r = self.record(self.start(), meta={**self.meta, 'captured_at_utc': '2026-11-07T23:00:00.000Z'})
        self.assertEqual(r['scheduled_start_utc'], '2026-11-08T00:40:00+00:00')
        self.assertEqual(pregame_records([r]), [r])

    def test_unknown_without_status_and_score_only(self):
        self.assertEqual(self.record()['event_status'], 'unknown')
        self.assertEqual(self.record(self.start(), True)['event_status'], 'unknown')

    def test_final_and_live_override_schedule(self):
        self.assertEqual(self.record(self.start() + '<span class="cb-event-cell__period">Final</span>')['event_status'], 'final')
        self.assertEqual(self.record(self.start() + '<svg data-testid="live-badge"></svg>')['event_status'], 'live')

    def test_unknown_timezone_and_past_start_excluded(self):
        self.assertIsNone(self.record(self.start(), meta=META)['scheduled_start_utc'])
        self.assertEqual(pregame_records([self.record(self.start('Today 5:00 PM'))]), [])

    def test_dates_dst_and_invalid_labels(self):
        self.assertEqual(scheduled_start('Tomorrow 3:40 PM', self.meta), '2026-11-08T20:40:00+00:00')
        self.assertEqual(scheduled_start('Fri Dec 25th 12:10 PM', self.meta), '2026-12-25T17:10:00+00:00')
        for text in ['TBD', 'Today 99:00 PM', 'Sat Dec 25th 12:10 PM']:
            self.assertIsNone(scheduled_start(text, self.meta))
        fall = {**self.meta, 'captured_at_utc': '2026-11-01T04:00:00+00:00'}
        spring = {**self.meta, 'captured_at_utc': '2026-03-08T05:00:00+00:00'}
        self.assertIsNone(scheduled_start('Today 1:30 AM', fall))
        self.assertIsNone(scheduled_start('Today 2:30 AM', spring))
        self.assertEqual(scheduled_start('Today 3:30 AM', spring), '2026-03-08T07:30:00+00:00')

    def test_score_extraction_preserves_76ers(self):
        html = self.fixture(scores=True).replace('OKC Thunder', 'PHI 76ers')
        self.assertEqual(parse_html(html, self.meta)['records'][0]['selections'][0]['team'], 'PHI 76ers')

    def test_duplicate_status_conflict_excluded(self):
        with self.assertRaises(ValueError):
            parse_html(self.fixture(self.start()) + self.fixture('<svg data-testid="live-badge"></svg>'), self.meta)

    def test_shared_day_wrapper_does_not_leak_live_status(self):
        live = self.fixture('<svg data-testid="live-badge"></svg>', True)
        pregame = self.fixture(self.start()).replace('34936963', '12345678')
        html = '<div class="cb-static-parlay__event-wrapper">' + live.removeprefix('<div class="cb-static-parlay__event-wrapper">').removesuffix('</div>') + pregame.removeprefix('<div class="cb-static-parlay__event-wrapper">').removesuffix('</div>') + '</div>'
        records = parse_html(html, self.meta)['records']
        self.assertEqual([r['event_status'] for r in records], ['live', 'pregame'])

    def test_verified_output_and_stale_output_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'metadata.json').write_text(json.dumps(self.meta), encoding='utf-8')
            (folder / 'page.html').write_text(self.fixture(self.start()), encoding='utf-8')
            text = 'OKC Thunder\nAT\nCLE Cavaliers\n+100\n+100\n+150\n+100\n+100\n−179\nToday 7:40 PM\nMore Bets'
            (folder / 'visible.txt').write_text(text, encoding='utf-8')
            self.assertEqual(process(folder), 0)
            self.assertEqual(len((folder / 'pregame_quotes.jsonl').read_text().splitlines()), 1)
            (folder / 'visible.txt').write_text(text.replace('+150', '+160'), encoding='utf-8')
            self.assertEqual(process(folder), 2)
            self.assertFalse((folder / 'pregame_quotes.jsonl').exists())
            self.assertFalse((folder / 'verification.json').exists())
            (folder / 'page.html').write_text('<h1>NBA Odds</h1>', encoding='utf-8')
            with self.assertRaises(ValueError):
                process(folder)
            self.assertFalse((folder / 'quotes.jsonl').exists())


if __name__ == '__main__': unittest.main()
