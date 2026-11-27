"""Estimate NBA pregame moneyline EV from a fixed offline observation set."""
from collections import defaultdict
import math

from history import encode, sha
from matching import NBAEventMatcher, inspect_pair
from moneyline_contract import ContractError, require, string, timestamp


def evaluate_moneyline(records, candidate_source, candidate_event_id, reference_source, *,
                       as_of_utc, event_reviews=(), rule_bindings=(),
                       max_age_seconds=120, max_skew_seconds=60, min_ev=0.02):
    """Select one source pair and return eligible estimates or rejection reasons."""
    for name, value in (('candidate_source', candidate_source),
                        ('candidate_event_id', candidate_event_id),
                        ('reference_source', reference_source)):
        string(value, name)
    require(candidate_source != reference_source, 'Choose distinct sources')
    for name, value in (('max_age_seconds', max_age_seconds),
                        ('max_skew_seconds', max_skew_seconds), ('min_ev', min_ev)):
        require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
                f'{name}: expected a finite nonnegative number')
    as_of = timestamp(as_of_utc, 'decision time')
    event_reviews = tuple(event_reviews)
    result = {
        'method': 'proportional-v1', 'as_of_utc': as_of.isoformat(),
        'candidate_source': candidate_source, 'candidate_event_id': candidate_event_id,
        'reference_source': reference_source, 'max_age_seconds': max_age_seconds,
        'max_skew_seconds': max_skew_seconds, 'min_ev': min_ev,
        'reasons': [], 'candidate': None, 'reference': None, 'skew_seconds': None,
        'canonical_event_id': None, 'scheduled_start_utc': None,
        'reference_overround': None, 'estimates': [], 'signals': [],
    }

    # Cut off future rows before indexing identities or selecting the newest quote.
    visible = []
    listings = defaultdict(list)
    for record in records:
        observed = timestamp(record['observed_at_utc'], 'observation time')
        if observed <= as_of:
            visible.append(record)
            listings[(record['source'], record['event']['source_event_id'])].append(
                (observed, record))

    def reject(*reasons):
        result['reasons'] = sorted(set(reasons))
        return result

    def latest(rows):
        newest = max(observed for observed, _ in rows)
        return [record for observed, record in rows if observed == newest]

    candidate_rows = listings.get((candidate_source, candidate_event_id))
    if not candidate_rows:
        return reject('missing_candidate')
    candidates = latest(candidate_rows)
    if len(candidates) != 1:
        return reject('ambiguous_latest_candidate')
    candidate = candidates[0]
    events = NBAEventMatcher(visible, event_reviews)
    _, _, teams, start = events.parts(candidate)
    if not teams or not start:
        return reject('unknown_or_duplicate_team' if not teams else 'unknown_schedule')

    # Select current listing states first. Ineligible newer states must not
    # resurrect an earlier available price or an earlier schedule.
    references = []
    for (source, _), rows in listings.items():
        if source != reference_source:
            continue
        current = latest(rows)
        if any(events.parts(record)[2:] == (teams, start) for record in current):
            references.extend(current)
    if not references:
        return reject('missing_reference')
    if len(references) != 1:
        return reject('ambiguous_reference')
    reference = references[0]

    try:
        match = inspect_pair(visible, candidate['observation_id'], reference['observation_id'],
                             event_reviews=event_reviews, rule_bindings=rule_bindings)
    except ContractError as exc:
        return reject(f'invalid_pair: {exc}')
    left, right = match['quotes']
    for role, record, matched in (('candidate', candidate, left),
                                  ('reference', reference, right)):
        result[role] = quote_provenance(record, as_of)
        result[role]['bound_rule_id'] = matched['bound_rule_id']
    reasons = match['reasons']
    if candidate['bookmaker'] == reference['bookmaker']:
        reasons.append('same_bookmaker')
    if reasons:
        return reject(*reasons)

    result['canonical_event_id'] = left['canonical_event_id']
    result['scheduled_start_utc'] = left['scheduled_start_utc']
    reasons = []
    for role in ('candidate', 'reference'):
        if result[role]['age_seconds'] > max_age_seconds:
            reasons.append(f'stale_{role}')
    if timestamp(left['scheduled_start_utc'], 'scheduled start') <= as_of:
        reasons.append('event_started')
    candidate_time = timestamp(candidate['observed_at_utc'], 'candidate observation time')
    reference_time = timestamp(reference['observed_at_utc'], 'reference observation time')
    skew = abs((candidate_time - reference_time).total_seconds())
    result['skew_seconds'] = skew
    if skew > max_skew_seconds:
        reasons.append('excessive_capture_skew')
    if reasons:
        return reject(*reasons)

    # Use canonical team IDs because the two boards can display opposite orders.
    reference_prices = dict(zip(right['outcome_team_ids'], result['reference']['selections']))
    implied = {team: 1 / quote['decimal_odds'] for team, quote in reference_prices.items()}
    total = sum(implied.values())
    result['reference_overround'] = total - 1
    for team, offer in zip(left['outcome_team_ids'], result['candidate']['selections']):
        probability = implied[team] / total
        ev = probability * offer['decimal_odds'] - 1
        estimate = {'team_id': team, 'fair_probability': probability, 'ev': ev,
                    'candidate_selection': offer, 'reference_selection': reference_prices[team]}
        result['estimates'].append(estimate)
        if ev > 0 and (ev >= min_ev or math.isclose(ev, min_ev, rel_tol=1e-12)):
            result['signals'].append(estimate)
    return result


def quote_provenance(record, as_of):
    observed = timestamp(record['observed_at_utc'], 'observation time')
    return {
        'observation_id': record['observation_id'], 'observation_sha256': sha(encode(record)),
        'run_id': record['run_id'], 'source': record['source'], 'bookmaker': record['bookmaker'],
        'source_event_id': record['event']['source_event_id'],
        'source_url': record['source_url'], 'observed_at_utc': observed.isoformat(),
        'source_updated_at_utc': record['source_updated_at_utc'],
        'age_seconds': (as_of - observed).total_seconds(),
        'provenance': dict(record['provenance']), 'verification': dict(record['verification']),
        'selections': [dict(selection) for selection in record['selections']],
    }
