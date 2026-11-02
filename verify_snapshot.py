"""Cross-check parsed prices against separately captured browser-visible text."""
import json
from pathlib import Path
import re
import sys

def verify(folder):
    folder = Path(folder)
    records = [json.loads(line) for line in (folder/'quotes.jsonl').read_text(encoding='utf-8').splitlines()]
    text = (folder/'visible.txt').read_text(encoding='utf-8')
    checks = []
    for record in records:
        a, b = record['teams_in_display_order']
        pattern = re.escape(a) + r'\nAT\n' + re.escape(b) + r'\n(.*?)\nMore Bets'
        matches = re.findall(pattern, text, flags=re.S)
        if len(matches) != 1:
            raise ValueError(f"{record['event_id']}: expected one visible matchup, found {len(matches)}")
        # Three columns x two team rows in the observed Game Lines board.
        prices = [line.strip() for line in matches[0].splitlines() if re.fullmatch(r'[+\-\u2212]\d+', line.strip())]
        if len(prices) != 6:
            raise ValueError(f"{record['event_id']}: expected six visible prices, found {len(prices)}")
        expected = [prices[2], prices[5]]
        actual = [s['odds_text'] for s in record['selections']]
        if expected != actual:
            raise ValueError(f"{record['event_id']}: visible moneylines {expected} differ from parsed {actual}")
        checks.append({'event_id':record['event_id'], 'teams':[a,b], 'moneylines':actual, 'matched':True})
    result = {'validated_events':len(checks), 'validated_selections':len(checks)*2,
              'method':'Compare event-bound HTML parser with separately captured rendered text; six-price board order',
              'checks':checks}
    (folder/'verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(f'PASS: {len(checks)} events / {len(checks)*2} prices match rendered text.')
    return result

if __name__ == '__main__':
    verify(sys.argv[1])
