# NBA moneyline observation contract, version 1

`moneyline_contract.validate_observation()` is the executable contract; the JSON
fixture in `tests/fixtures/moneyline-v1.json` is entirely synthetic. This document
specifies the persisted observation boundary for Day 3. It does not change the
current collector's JSONL format. Conversion and historical ingestion are next.

One observation contains the complete two-selection market at one capture time.
Every field in the fixture is required; unresolved values use explicit nulls.
Unexpected fields and future schema versions are rejected until migrated.

| Field group | Meaning |
| --- | --- |
| schema_version | Integer 1; version changes require an explicit migration |
| observation_id / run_id | Immutable observation and capture identifiers; storage must enforce uniqueness and collision checks |
| source / bookmaker / source_url | Displaying source, attributed price owner, and actual public source URL; keep distinct for any future aggregator |
| observed_at_utc | When DOM and rendered text were captured together; never infer book update time |
| source_updated_at_utc | Explicit source update timestamp only; otherwise null |
| event | NBA source ID, nullable canonical ID/season, two original participant names with nullable canonical IDs and explicit unknown roles, displayed/resolved schedule, and status evidence |
| market | Moneyline, no handicap, full_game or unknown period, nullable overtime semantics, versioned rule ID/status, and original label |
| selections | Exactly one outcome per participant_index (0/1); source IDs may be null; retain original odds and format alongside finite decimal price > 1 |
| provenance | Parser version, original HTML hash, capture start, nullable observed region, and portable relative evidence path |
| verification | passed/failed/unverified price check and method; kept separate from rule review and event matching |

Participant list order is source display order, not an assertion of home/away.
Canonical identity and season remain null until explicitly resolved. A timezone-
unresolved start stays null. Unknown settlement semantics remain unknown even
when a general rules page has been reviewed; quote-to-rule binding is separate.

Unavailable outcomes retain their original display text if present, but decimal
price must be null. Available original and normalized prices must agree. UTC
instants must include a timezone and have zero offset. Source updates cannot be
later than capture; capture start cannot be later than observation. A known
pregame start must be later than observation. Live/final/unknown observations may
be retained for history and must be filtered out by the future pregame engine.

For Day 3: derive a deterministic observation ID from source, original evidence
hash, source event/market identity, capture time, parser version and schema
version. Reimporting that observation is idempotent. A later capture is distinct
even if prices do not move. A changed parse produces a new version linked to the
superseded observation in the storage layer, rather than overwriting history.
Run manifests will hold batch checksums, completion status, and device identity;
those belong to the storage envelope rather than each quote.

Validation is structural and semantic consistency only. It does not authenticate
source data or certify EV eligibility. Future importers must verify evidence and
price-check claims themselves; future matching must resolve canonical events,
rule applicability and reviewed cross-book compatibility. The validator does not
look up rule IDs or infer that two books share settlement behavior.

Examples/checks:

```sh
.venv/bin/python moneyline_contract.py tests/fixtures/moneyline-v1.json
.venv/bin/python -m unittest -v
```
