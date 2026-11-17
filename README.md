# Bringing Down the House

Collect DraftKings NBA moneylines with clean team names, separate scores, explicit event status, and verified pregame output. Cross-book analysis and settlement-rule matching are future work.

## Setup and run (macOS / Linux)

From a terminal in this repository:

```sh
./setup.sh
./run.sh
```

Setup creates `.venv`, installs the pinned dependencies, and downloads Playwright's
Chromium into `.browsers`. It uses Python 3.10+ from `python3`, or, if unavailable,
uses an installed `uv` to obtain Python 3.12 in the project-local `.python` folder.
To select another installed interpreter, use `PYTHON=/path/to/python3 ./setup.sh`.
If neither compatible Python nor uv is installed, setup explains what is missing.
An existing Windows `.venv` cannot be reused on Unix; move it aside before setup.
Do not copy virtual environments between devices.

Each run opens Chromium and saves a timestamped snapshot under `data/`. No shell
activation is needed. Both scripts resolve paths from their own location, so they
also work when launched by absolute path from another directory. Relative
`--output` paths are relative to the repository. Internet access is needed for
setup downloads and live collection; headed collection requires a desktop session.
Linux may additionally need system browser libraries: use
`.venv/bin/python -m playwright install-deps chromium` on a supported distribution
(this can require administrator access).

Extra capture options are forwarded to the Python collector:

```sh
./run.sh --help
./run.sh --output data/manual
./run.sh --channel chrome    # use an already installed Google Chrome
./run.sh --channel msedge    # use an already installed Microsoft Edge
```

The default uses bundled Chromium, so Edge is not required. Direct equivalent:

```sh
PLAYWRIGHT_BROWSERS_PATH="$PWD/.browsers" .venv/bin/python scraper.py capture --method browser --headed --via-home
```

Local runtime folders (`.venv`, `.browsers`, `.python`, `.cache`) and captured data
are ignored by Git. The existing PowerShell scripts remain available on Windows:
`./setup.ps1`, then `./run.ps1` (requires installed Edge). The Python collector and
pinned dependencies are shared across platforms.

## Results

- `quotes.jsonl`: validated two-selection moneyline cards, including live and unknown-status events. Team names exclude scoreboard text; scores are in `scores_in_display_order`.
- `pregame_quotes.jsonl`: only future, pregame events with a resolved UTC start and two available prices, emitted after all parsed quotes pass rendered-text verification. An empty file means no eligible events; an absent file means verification was unavailable or failed.
- `report.json`: parsing issues, counts by status, missing start times, verification status, and verified pregame count. Counts describe emitted records; malformed or incomplete markets are excluded and reported as issues.
- `verification.json`: price checks against separately represented browser-visible text.
- `page.html`, `visible.txt`, `page.png`: source evidence. HTML and visible text are read together; the screenshot is taken afterward and may show subsequent changes.
- `metadata.json`: capture timestamp, browser timezone, source details, and HTML hash.

`event_status` is `pregame`, `live`, `final`, or `unknown`, based on event-bound page evidence. Scoreboard numbers alone do not establish live status. `status_evidence` and `scheduled_start_text` preserve the observed labels.

UTC conversion uses the recorded browser timezone and capture date. Supported labels are Today, Tomorrow, and weekday/month/day labels within the next 183 days. Ambiguous daylight-saving times, invalid labels, and captures without a recorded timezone retain a null UTC start. Older snapshots therefore remain useful for verifying prices and status, but do not enter the verified pregame dataset. UTC times reflect the displayed schedule, not an independently confirmed league schedule.

Exit code 0 means parsing and verification completed without issues; 2 means review `report.json` (valid pregame output may still exist when a different market was excluded); 1 means capture or parsing failed. Partial live markets can legitimately produce warnings. Unknown layouts and failed verification never populate pregame output. Failed reparsing removes stale derived quote and verification files.

## Offline validation

```sh
.venv/bin/python scraper.py parse data/YOUR_SNAPSHOT
.venv/bin/python verify_snapshot.py data/YOUR_SNAPSHOT
.venv/bin/python -m unittest -v
```

Rendered-text verification expects the observed six-price Spread/Total/Moneyline board. A changed layout or unavailable prices can cause verification to fail and withhold the pregame file. Collection remains on demand; access restrictions can interrupt it. Visible Chromium on macOS was verified on 2026-11-17: 5 pregame events / 10 prices matched rendered text, with no reported issues. The 18 existing unit tests also passed. Visible Edge on Windows was the original tested path. Linux scripts are provided but have not been exercised on a Linux host. Source access and layouts can change; inspect each capture report.

Capture time is not the sportsbook's last update time. Records are historical snapshots, not a guarantee that prices remain available. Game settlement rules are still unverified; these outputs are not yet suitable for cross-book arbitrage calculations.


## Source feasibility and observation contract

The existing `./run.sh` remains the working DraftKings collector. Source probing
saves public-page evidence for review; it does **not** parse or validate new books:

```sh
.venv/bin/python source_probe.py pinnacle
.venv/bin/python source_probe.py pinnacle_rules draftkings_rules
.venv/bin/python source_probe.py fanduel betway
```

Probes use headed Chromium by default (`--headless` is optional), bounded timeouts,
and unique folders under `data/source_probes/` containing HTML, rendered text,
screenshot, and metadata. HTTP 200 means `captured_unreviewed`, not verified odds;
failed navigation or HTTP errors retain available evidence and return exit 1.
Probe output stays out of Git. Source IDs/URLs are in `config/sources.json`.

The 2026-11-17 local review selected Pinnacle for the reference adapter and retained
DraftKings as the initial comparison book. Pinnacle rendered five NBA matchups in
two separate captures. FanDuel is the preferred additional comparison candidate;
Betway displayed prices but also a location prompt and differing start times.
See `config/source_review.json` for exact provenance and unresolved limitations.
Only DraftKings currently has a functioning verified odds parser.

`config/settlement_rules.json` records separate reviewed rule-page profiles. Both
books include overtime for standard full-game NBA moneylines, but interruption
and void conditions differ. These profiles do not automatically assign rules to
existing quotes or approve cross-book comparisons.

The next storage layer will use the v1 observation contract described in
`config/moneyline-v1.md` and enforced by `moneyline_contract.py`:

```sh
.venv/bin/python moneyline_contract.py tests/fixtures/moneyline-v1.json
.venv/bin/python -m unittest -v
```

The JSON fixture is synthetic. The small Pinnacle HTML fixture is a reduced
observed row for future parser work, not a live offer. Contract validation checks
structure, timestamps, outcome identity, and price consistency; it does not
certify EV eligibility. Existing collector JSONL is unchanged, and conversion,
SQLite ingestion, and shared archive batches are not implemented yet.
