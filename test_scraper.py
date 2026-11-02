import unittest
from scraper import parse_html, decimal_odds, URL

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

if __name__ == '__main__': unittest.main()
