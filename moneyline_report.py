"""Write a local audit report for the current NBA moneyline collection."""
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path

from history import atomic_publish, load_batch
from moneyline_contract import timestamp
from moneyline_ev import evaluate_moneyline, quote_provenance


COLUMNS = (
    'row_type', 'status', 'reason', 'source', 'phase', 'snapshot', 'manifest',
    'observations', 'verified_pregame', 'issues', 'as_of_utc', 'candidate_event_id',
    'canonical_event_id', 'scheduled_start_utc', 'market', 'period', 'line', 'team_id',
    'candidate_decimal_odds', 'reference_decimal_odds', 'fair_probability', 'ev',
    'reference_overround', 'skew_seconds', 'method', 'max_age_seconds',
    'max_skew_seconds', 'min_ev', 'candidate_quotes', 'reference_quote',
    'candidate_listings', 'eligible_comparisons', 'qualifying_outcomes',
)


def report_moneylines(collection, archive, output, reviews):
    decision_time = datetime.now(timezone.utc)
    as_of = decision_time.isoformat()
    rows = []
    records = []
    failures = 0
    for source in collection['sources']:
        row = {key: source[key] for key in
               ('status', 'source', 'snapshot', 'manifest', 'phase',
                'observations', 'verified_pregame') if key in source}
        row.update(row_type='source', as_of_utc=as_of, reason=source.get('error'),
                   issues=json.dumps(source.get('issues', [])))
        rows.append(row)
        if source['status'] == 'completed':
            # Use only imported batches from this invocation. Old history must
            # not fill a gap left by a failed source or a missing current listing.
            _, _, batch = load_batch(archive, Path(source['manifest']))
            records.extend(batch)
        else:
            failures += 1
        print(f"{row['source']}: {row['status']}"
              + (f" ({row['phase']}: {row['reason']})" if row['reason']
                 else f" ({row['observations']} observations)"))

    candidates = defaultdict(list)
    for record in records:
        if (record['source'] == 'draftkings'
                and timestamp(record['observed_at_utc'], 'observation time') <= decision_time):
            candidates[record['event']['source_event_id']].append(record)
    reasons = Counter()
    eligible = qualifying = 0
    for event_id, listings in sorted(candidates.items()):
        result = evaluate_moneyline(records, 'draftkings', event_id, 'pinnacle',
                                    as_of_utc=as_of, event_reviews=reviews['events'],
                                    rule_bindings=reviews['rule_bindings'])
        row = {key: result[key] for key in
               ('as_of_utc', 'candidate_event_id', 'canonical_event_id', 'scheduled_start_utc',
                'method', 'max_age_seconds', 'max_skew_seconds', 'min_ev',
                'reference_overround', 'skew_seconds')}
        # Retain the originating listings even when matching stops before
        # selecting a pair, such as a missing reference or a candidate tie.
        quotes = ([result['candidate']] if result['candidate'] else
                  [quote_provenance(record, decision_time) for record in listings])
        market = quotes[0]['market']
        row.update(row_type='comparison', market=market['type'], period=market['period'],
                   line=market['line'], candidate_quotes=json.dumps(quotes),
                   reference_quote=json.dumps(result['reference']))
        if len(quotes) == 1 and row['scheduled_start_utc'] is None:
            row['scheduled_start_utc'] = quotes[0]['event']['scheduled_start_utc']
        reference = result['reference']
        if reference and row['skew_seconds'] is None:
            offered_at = timestamp(quotes[0]['observed_at_utc'], 'candidate observation time')
            reference_at = timestamp(reference['observed_at_utc'], 'reference observation time')
            row['skew_seconds'] = abs((offered_at - reference_at).total_seconds())
        if result['reasons']:
            reasons.update(result['reasons'])
            rows.append(dict(row, status='rejected', reason='; '.join(result['reasons'])))
            continue
        eligible += 1
        qualifying += len(result['signals'])
        for estimate in result['estimates']:
            qualifies = estimate in result['signals']
            rows.append(dict(
                row, status='qualifying' if qualifies else 'below_threshold',
                reason='' if qualifies else 'ev_below_display_threshold',
                team_id=estimate['team_id'], fair_probability=estimate['fair_probability'],
                ev=estimate['ev'], candidate_decimal_odds=estimate['candidate_selection']['decimal_odds'],
                reference_decimal_odds=estimate['reference_selection']['decimal_odds'],
            ))

    if failures:
        status = ('collection_failed' if failures == len(collection['sources'])
                  else 'partial_collection_failure')
    elif not candidates:
        status = 'no_candidate_listings'
    elif not eligible:
        status = 'no_eligible_comparisons'
    elif not qualifying:
        status = 'no_qualifying_outcomes'
    else:
        status = 'qualifying_outcomes'
    rows.append(dict(row_type='summary', status=status, as_of_utc=as_of,
                     candidate_listings=len(candidates), eligible_comparisons=eligible,
                     qualifying_outcomes=qualifying))
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    atomic_publish(output, buffer.getvalue().encode('utf-8'))
    print(f'{status.replace("_", " ").capitalize()}. {len(candidates)} candidate listings, '
          f'{eligible} eligible comparisons, {qualifying} qualifying outcomes.')
    for reason, count in sorted(reasons.items()):
        print(f'  {reason}: {count}')
    print(f'Database: {collection["database"]}')
    print(f'CSV: {output}')
    return int(bool(failures))
