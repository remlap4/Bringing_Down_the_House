"""Resolve two archived NBA moneylines and check settlement compatibility offline."""
import argparse
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sqlite3

from history import encode, identifier, sha
from moneyline_contract import fields, require, string, timestamp, validate_observation

CONFIG = Path(__file__).resolve().parent / 'config'


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def alias_key(value):
    return ' '.join(value.casefold().split())


def schedule_key(value):
    start = timestamp(value, 'scheduled start', nullable=True)
    return start.isoformat() if start else None


def event_id(teams, original_start):
    return 'nba:' + identifier('nba-event-v1', sorted(teams), original_start)


@dataclass(frozen=True)
class EventReview:
    teams: tuple
    starts: tuple

    @property
    def canonical_id(self):
        return event_id(self.teams, self.starts[0])


class NBAEventMatcher:
    def __init__(self, records, event_reviews=()):
        self.aliases = {}
        for team, aliases in read_json(CONFIG / 'nba_teams.json').items():
            for alias in aliases:
                key = alias_key(alias)
                require(key not in self.aliases, f'Duplicate NBA alias {alias}')
                self.aliases[key] = team

        self.mappings = {}
        self.reserved = set()
        for entry in event_reviews:
            fields(entry, 'teams scheduled_starts source_events evidence', 'event review')
            string(entry['evidence'], 'event review evidence')
            teams = entry['teams']
            require(isinstance(teams, list) and len(teams) == 2
                    and all(team in self.aliases.values() for team in teams)
                    and teams[0] != teams[1], 'Expected two distinct canonical NBA teams')
            require(isinstance(entry['scheduled_starts'], list) and entry['scheduled_starts'],
                    'Expected at least one reviewed scheduled start')
            starts = tuple(schedule_key(start) for start in entry['scheduled_starts'])
            require(None not in starts and len(starts) == len(set(starts)),
                    'Reviewed starts must be distinct known UTC instants')
            review = EventReview(tuple(sorted(teams)), starts)
            slots = {(review.teams, start) for start in starts}
            require(self.reserved.isdisjoint(slots), 'Overlapping reviewed event schedules')
            self.reserved.update(slots)
            require(isinstance(entry['source_events'], list) and entry['source_events'],
                    'Expected at least one reviewed source mapping')
            for source_event in entry['source_events']:
                fields(source_event, 'source source_event_id', 'source event mapping')
                for field in source_event:
                    string(source_event[field], field)
                key = (source_event['source'], source_event['source_event_id'])
                require(key not in self.mappings, 'Duplicate reviewed source event mapping')
                self.mappings[key] = review

        # Index all active records so a pair cannot hide a conflicting third listing.
        self.source_slots = defaultdict(set)
        self.slot_ids = defaultdict(set)
        for record in records:
            source, key, teams, start = self.parts(record)
            if teams and start:
                self.source_slots[key].add((teams, start))
                self.slot_ids[(source, teams, start)].add(key[1])

    def parts(self, record):
        event = record['event']
        source = record['source']
        teams = [self.aliases.get(alias_key(team['source_name'])) for team in event['participants']]
        known = None not in teams and teams[0] != teams[1]
        return (source, (source, event['source_event_id']), tuple(sorted(teams)) if known else None,
                schedule_key(event['scheduled_start_utc']))

    def resolve(self, record):
        source, key, teams, start = self.parts(record)
        review = self.mappings.get(key)
        if not teams:
            return None, 'unknown_or_duplicate_team'
        if not start:
            return None, 'unknown_schedule'
        if len(self.slot_ids[(source, teams, start)]) > 1:
            return None, 'duplicate_source_listing'
        slots = self.source_slots[key]
        if review:
            if (teams != review.teams or start not in review.starts
                    or any(pair != teams or when not in review.starts for pair, when in slots)):
                return None, 'source_mapping_conflict'
            return review, None
        if len(slots) > 1:
            return None, 'unreviewed_schedule_or_participant_change'
        if (teams, start) in self.reserved:
            return None, 'unmapped_source_event'
        return EventReview(teams, (start,)), None


class SettlementRules:
    def __init__(self, bindings=()):
        registry = read_json(CONFIG / 'settlement_rules.json')
        self.profiles = {profile['id']: profile for profile in registry['profiles']
                         if profile['page_review_status'] == 'reviewed'}
        self.comparisons = {frozenset(row['rule_ids']): row for row in registry['comparisons']}
        self.bindings = {}
        for binding in bindings:
            fields(binding, 'observation_id observation_sha256 rule_id evidence', 'rule binding')
            for key in binding:
                string(binding[key], key)
            require(re.fullmatch(r'[0-9a-f]{64}', binding['observation_sha256']),
                    'Invalid rule binding observation digest')
            observation_id = binding['observation_id']
            require(observation_id not in self.bindings, 'Duplicate observation rule binding')
            require(binding['rule_id'] in self.profiles, 'Unknown rule profile in binding')
            self.bindings[observation_id] = binding

    def bind(self, record):
        # Bind the full archived payload. An ID alone does not cover all quote fields.
        binding = self.bindings.get(record['observation_id'])
        if not binding:
            return None, 'missing_quote_rule_review'
        if sha(encode(record)) != binding['observation_sha256']:
            return None, 'quote_rule_review_digest_mismatch'
        rule_id = binding['rule_id']
        profile = self.profiles[rule_id]
        market = record['market']
        if (profile['bookmaker'] != record['bookmaker']
                or market['settlement_rule_id'] not in (None, rule_id)
                or market['period'] not in ('unknown', profile['period'])
                or market['overtime_included'] not in (None, profile['overtime_included'])):
            return None, 'quote_rule_profile_conflict'
        return rule_id, None

    def compatibility(self, left, right):
        profile_ids = [quote['bound_rule_id'] for quote in (left, right)]
        if None in profile_ids:
            return 'unknown'
        if profile_ids[0] == profile_ids[1]:
            return 'compatible'
        row = self.comparisons.get(frozenset(profile_ids))
        if row and row['full_settlement_equivalence'] is False:
            return 'incompatible'
        if row and row['full_settlement_equivalence'] is True and row['analysis_compatibility'] == 'approved':
            return 'compatible'
        return 'unknown'


def load_history(database):
    uri = database.resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        return [json.loads(payload) for (payload,) in connection.execute("""
            SELECT payload FROM observations
            WHERE observation_id NOT IN (SELECT supersedes_observation_id FROM observation_supersessions)
        """)]


def inspect_pair(records, left_id, right_id, *, event_reviews=(), rule_bindings=()):
    require(left_id != right_id, 'Choose two distinct observations')
    by_id = {record['observation_id']: record for record in records}
    require(left_id in by_id and right_id in by_id, 'Observation missing or superseded')
    pair = [validate_observation(by_id[key]) for key in (left_id, right_id)]
    events = NBAEventMatcher(by_id.values(), event_reviews)
    rules = SettlementRules(rule_bindings)
    quotes = []
    reasons = []
    for record in pair:
        review, event_error = events.resolve(record)
        rule_id, rule_error = rules.bind(record)
        event = record['event']
        if event['status'] != 'pregame' or record['verification']['price_check'] != 'passed':
            reasons.append('unverified_pregame')
        if any(selection['status'] != 'available' for selection in record['selections']):
            reasons.append('unavailable_selection')
        reasons.extend(reason for reason in (event_error, rule_error) if reason)
        teams = [events.aliases.get(alias_key(team['source_name'])) for team in event['participants']]
        quotes.append({
            'observation_id': record['observation_id'],
            'source': record['source'],
            'source_event_id': event['source_event_id'],
            'canonical_event_id': review.canonical_id if review else None,
            'scheduled_start_utc': schedule_key(event['scheduled_start_utc']),
            'schedule_history_utc': list(review.starts) if review else [],
            'outcome_team_ids': [teams[selection['participant_index']]
                                 for selection in record['selections']] if review else [],
            'bound_rule_id': rule_id,
            'event_issue': event_error,
            'rule_issue': rule_error,
        })

    left, right = quotes
    if left['canonical_event_id'] != right['canonical_event_id']:
        reasons.append('different_events')
    if left['scheduled_start_utc'] != right['scheduled_start_utc']:
        reasons.append('different_scheduled_starts')
    # Display order carries no role information. Reject only explicit role conflicts.
    roles = defaultdict(set)
    for record in pair:
        for team in record['event']['participants']:
            if team['role'] != 'unknown':
                roles[team['role']].add(events.aliases.get(alias_key(team['source_name'])))
    if any(len(teams) > 1 for teams in roles.values()) or roles['home'] & roles['away']:
        reasons.append('conflicting_participant_roles')

    compatibility = rules.compatibility(left, right)
    if compatibility != 'compatible':
        reasons.append(f'settlement_{compatibility}')
    elif (rules.profiles[left['bound_rule_id']]['overtime_included']
          != rules.profiles[right['bound_rule_id']]['overtime_included']
          or rules.profiles[left['bound_rule_id']]['period']
          != rules.profiles[right['bound_rule_id']]['period']):
        reasons.append('different_market_semantics')
    return {'comparable': not reasons, 'settlement_compatibility': compatibility,
            'reasons': sorted(set(reasons)), 'quotes': quotes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('left_id')
    parser.add_argument('right_id')
    parser.add_argument('--database', type=Path, default=Path('data/local/odds.sqlite'))
    parser.add_argument('--reviews', type=Path, help='JSON with reviewed event mappings and rule bindings')
    args = parser.parse_args()
    try:
        reviews = read_json(args.reviews) if args.reviews else {'events': [], 'rule_bindings': []}
        fields(reviews, 'events rule_bindings', 'matching reviews')
        require(all(isinstance(value, list) for value in reviews.values()), 'Expected review lists')
        result = inspect_pair(load_history(args.database), args.left_id, args.right_id,
                              event_reviews=reviews['events'], rule_bindings=reviews['rule_bindings'])
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f'Matching error: {exc}\n')
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
