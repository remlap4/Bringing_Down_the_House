"""Version 1 NBA moneyline observation contract (stdlib only).

Validation permits unresolved identity/rules in historical storage; it never
certifies an observation for cross-book analysis. See config/moneyline-v1.md.
"""
import argparse
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import re
from urllib.parse import urlparse


class ContractError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ContractError(message)


def fields(value, expected, path):
    require(isinstance(value, dict), f'{path}: expected object')
    require(set(value) == set(expected.split()), f'{path}: missing or unexpected fields')


def string(value, path, nullable=False):
    if nullable and value is None:
        return
    require(isinstance(value, str) and bool(value.strip()), f'{path}: expected nonempty string')


def timestamp(value, path, nullable=False):
    if nullable and value is None:
        return None
    string(value, path)
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ContractError(f'{path}: invalid timestamp') from exc
    require('T' in value and parsed.utcoffset() == timedelta(0), f'{path}: UTC timestamp required')
    return parsed


def url(value, path):
    string(value, path)
    parsed = urlparse(value)
    require(parsed.scheme in ('https', 'http') and bool(parsed.hostname)
            and parsed.username is None and parsed.password is None, f'{path}: expected public HTTP(S) URL')


def validate_observation(record):
    fields(record, 'schema_version observation_id run_id source bookmaker source_url observed_at_utc source_updated_at_utc event market selections provenance verification', 'observation')
    require(type(record['schema_version']) is int and record['schema_version'] == 1, 'unsupported schema_version')
    for key in ('observation_id', 'run_id', 'source', 'bookmaker'):
        string(record[key], key)
    url(record['source_url'], 'source_url')
    observed = timestamp(record['observed_at_utc'], 'observed_at_utc')
    updated = timestamp(record['source_updated_at_utc'], 'source_updated_at_utc', nullable=True)
    require(updated is None or updated <= observed, 'source update cannot be after observation')

    event = record['event']
    fields(event, 'league source_event_id canonical_event_id season participants scheduled_start_utc scheduled_start_text status status_evidence', 'event')
    require(event['league'] == 'NBA', 'v1 supports NBA only')
    string(event['source_event_id'], 'source_event_id')
    for key in ('canonical_event_id', 'season', 'scheduled_start_text', 'status_evidence'):
        string(event[key], key, nullable=True)
    start = timestamp(event['scheduled_start_utc'], 'scheduled_start_utc', nullable=True)
    require(isinstance(event['status'], str) and event['status'] in ('pregame', 'live', 'final', 'unknown'), 'invalid event status')
    if event['status'] == 'pregame' and start is not None:
        require(start > observed, 'pregame start must be after observation')
    teams = event['participants']
    require(isinstance(teams, list) and len(teams) == 2, 'exactly two participants required')
    for team in teams:
        fields(team, 'source_name canonical_team_id role', 'participant')
        string(team['source_name'], 'source_name')
        string(team['canonical_team_id'], 'canonical_team_id', nullable=True)
        require(isinstance(team['role'], str) and team['role'] in ('home', 'away', 'unknown'), 'invalid participant role')
    names = [t['source_name'] for t in teams]
    require(len({n.strip().casefold() for n in names}) == 2, 'participants must be distinct')
    ids = [t['canonical_team_id'] for t in teams if t['canonical_team_id'] is not None]
    require(len(ids) == len(set(ids)), 'duplicate canonical team')
    roles = [t['role'] for t in teams if t['role'] != 'unknown']
    require(len(roles) == len(set(roles)), 'duplicate home/away role')

    market = record['market']
    fields(market, 'type period line overtime_included settlement_rule_id rule_status source_label', 'market')
    require(market['type'] == 'moneyline' and market['line'] is None, 'v1 supports unhandicapped moneyline only')
    require(isinstance(market['period'], str) and market['period'] in ('full_game', 'unknown'), 'v1 supports full-game or unresolved period only')
    require(market['overtime_included'] is None or type(market['overtime_included']) is bool, 'invalid overtime flag')
    require(isinstance(market['rule_status'], str) and market['rule_status'] in ('unverified', 'reviewed'), 'invalid rule_status')
    string(market['settlement_rule_id'], 'settlement_rule_id', nullable=True)
    string(market['source_label'], 'source_label')
    if market['rule_status'] == 'reviewed':
        require(market['settlement_rule_id'] is not None and market['period'] == 'full_game'
                and market['overtime_included'] is not None, 'reviewed rules require identity, period and overtime semantics')

    selections = record['selections']
    require(isinstance(selections, list) and len(selections) == 2, 'complete two-selection market required')
    seen = set()
    selection_ids = set()
    for selection in selections:
        fields(selection, 'source_selection_id participant_index odds_text odds_format decimal_odds status', 'selection')
        string(selection['source_selection_id'], 'source_selection_id', nullable=True)
        if selection['source_selection_id'] is not None:
            require(selection['source_selection_id'] not in selection_ids, 'duplicate selection ID')
            selection_ids.add(selection['source_selection_id'])
        index = selection['participant_index']
        require(type(index) is int and index in (0, 1) and index not in seen, 'selection must map once to each participant')
        seen.add(index)
        require(isinstance(selection['status'], str) and selection['status'] in ('available', 'unavailable'), 'invalid price status')
        require(isinstance(selection['odds_format'], str) and selection['odds_format'] in ('american', 'decimal'), 'unsupported odds format')
        string(selection['odds_text'], 'odds_text', nullable=selection['status'] == 'unavailable')
        odds = selection['decimal_odds']
        if selection['status'] == 'unavailable':
            require(odds is None, 'unavailable selection cannot carry an available decimal price')
            continue
        require(type(odds) in (int, float) and math.isfinite(odds) and odds > 1, 'decimal odds must be finite and > 1')
        if selection['odds_format'] == 'american':
            from scraper import decimal_odds
            try:
                expected = decimal_odds(selection['odds_text'])
            except ValueError as exc:
                raise ContractError('invalid original American odds') from exc
        else:
            try:
                expected = float(selection['odds_text'])
            except ValueError as exc:
                raise ContractError('invalid original decimal odds') from exc
        require(math.isfinite(expected) and math.isclose(odds, expected, rel_tol=1e-9), 'original and normalized odds disagree')

    provenance = record['provenance']
    fields(provenance, 'parser_version html_sha256 capture_started_at_utc region evidence_path', 'provenance')
    for key in ('parser_version', 'evidence_path'):
        string(provenance[key], key)
    string(provenance['region'], 'region', nullable=True)
    require(isinstance(provenance['html_sha256'], str) and re.fullmatch(r'[0-9a-f]{64}', provenance['html_sha256']) is not None, 'invalid HTML SHA256')
    capture_start = timestamp(provenance['capture_started_at_utc'], 'capture_started_at_utc')
    require(capture_start <= observed, 'capture cannot start after observation')
    evidence = provenance['evidence_path']
    require(not evidence.startswith(('/', '\\')) and '\\' not in evidence and ':' not in evidence
            and '..' not in evidence.split('/'), 'evidence_path must be a portable repository-relative path')
    verification = record['verification']
    fields(verification, 'price_check method', 'verification')
    require(isinstance(verification['price_check'], str) and verification['price_check'] in ('passed', 'failed', 'unverified'), 'invalid price verification')
    string(verification['method'], 'verification.method', nullable=True)
    if verification['price_check'] == 'passed':
        require(verification['method'] is not None, 'passed verification requires a method')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path, help='JSON observation or list of observations')
    args = parser.parse_args()
    try:
        data = json.loads(args.file.read_text(encoding='utf-8'))
        records = data if isinstance(data, list) else [data]
        require(bool(records), 'empty observation list')
        for record in records:
            validate_observation(record)
    except (ValueError, OSError) as exc:
        parser.exit(1, f'Invalid contract: {exc}\n')
    print(f'PASS: {len(records)} v1 observations; structural validation only, not EV eligibility.')


if __name__ == '__main__':
    main()
