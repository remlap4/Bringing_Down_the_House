"""Independently check event-bound moneylines against browser-visible text."""
import json
from pathlib import Path
import re
import sys


def verify(folder):
    folder = Path(folder)
    (folder / 'verification.json').unlink(missing_ok=True)
    records = [json.loads(line) for line in (folder / 'quotes.jsonl').read_text(encoding='utf-8').splitlines()]
    result = verify_records(records, (folder / 'visible.txt').read_text(encoding='utf-8'))
    (folder / 'verification.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(f"PASS: {result['validated_events']} events / {result['validated_selections']} prices match rendered text.")
    return result


def verify_records(records, text):
    if not records:
        raise ValueError('No quotes to verify')
    text = '\n'.join(line.strip() for line in text.splitlines() if line.strip())
    checks = []
    for record in records:
        a, b = record['teams_in_display_order']
        scores = record.get('scores_in_display_order', [None, None])
        def team_pattern(team, score):
            return re.escape(team) + (r'\n' + str(score) if score is not None else '')
        pattern = (r'(?:^|\n)' + team_pattern(a, scores[0]) + r'\nAT\n'
                   + team_pattern(b, scores[1]) + r'\n(.*?)\nMore Bets(?:\n|$)')
        matches = re.findall(pattern, text, flags=re.S)
        if len(matches) != 1:
            raise ValueError(f"{record['event_id']}: expected one visible matchup, found {len(matches)}")
        lines = matches[0].splitlines()
        prices = [line for line in lines if re.fullmatch(r'[+\-\u2212]\d+|EVEN|EVS', line)]
        if len(prices) != 6:
            raise ValueError(f"{record['event_id']}: expected six visible prices, found {len(prices)}")
        expected = [prices[2], prices[5]]
        actual = [s['odds_text'] for s in record['selections']]
        if expected != actual:
            raise ValueError(f"{record['event_id']}: visible moneylines {expected} differ from parsed {actual}")
        label = record.get('scheduled_start_text')
        if label and label not in lines:
            raise ValueError(f"{record['event_id']}: schedule label not in rendered text")
        checks.append({'event_id': record['event_id'], 'teams': [a, b], 'moneylines': actual, 'matched': True})
    return {'validated_events': len(checks), 'validated_selections': len(checks) * 2,
              'method': 'Event-bound rendered text; six-price board order; separate score lines', 'checks': checks}


if __name__ == '__main__':
    verify(sys.argv[1])
