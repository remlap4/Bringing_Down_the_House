# Setup and operating instructions

For the project scope and approach, see [README.md](README.md). This guide covers
installation, collection, output interpretation, validation, and historical storage.

## Setup and run (macOS / Linux)

From a terminal in this repository:

```sh
./setup.sh
.venv/bin/python run.py collect --sport nba
```

Setup creates `.venv`, installs the pinned dependencies, and downloads Playwright's
Chromium into `.browsers`. It uses Python 3.10+ from `python3`, or, if unavailable,
uses an installed `uv` to obtain Python 3.12 in the project-local `.python` folder.
To select another installed interpreter, use `PYTHON=/path/to/python3 ./setup.sh`.
If neither compatible Python nor uv is installed, setup explains what is missing.
An existing Windows `.venv` cannot be reused on Unix; move it aside before setup.
Do not copy virtual environments between devices.

Use the command above for collection through SQLite import. For a standalone
DraftKings snapshot and source-specific outputs, use `./run.sh`. The standalone
launcher opens Chromium and saves a timestamped snapshot under `data/`. No shell
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

## Collect into history

Run from the repository root. Use `./.venv/Scripts/python.exe run.py collect --sport
nba --channel msedge` on Windows after PowerShell setup. The command opens a visible
browser by default and goes directly to the DraftKings NBA page. Use `--via-home`
to follow the NBA link from the homepage. Use `--headless` to run without a visible
window or `--channel chrome` or `--channel msedge` to use an installed browser.
Direct navigation has been exercised with mocked browser responses.

Select Pinnacle with the same Python environment and command:

```sh
.venv/bin/python run.py collect --sport nba --source pinnacle
.venv/bin/python run.py collect --sport nba --source pinnacle --snapshot data/source_probes/CAPTURE_DIRECTORY
```

The second command replays a saved capture without opening a browser. Use a
directory under the repository root containing `metadata.json`, `page.html`,
and `visible.txt`. Replay retains the original observation time.

The adapter accepts the supported English decimal NBA board layout with Today
or Tomorrow date groups. It compares both prices in display order against
rendered text and checks the displayed clock against the capture timezone
before resolving future starts. If any row in a supported pregame table is
incomplete, unavailable, ambiguous, or mismatched, the source fails validation
and publishes no batch. Two saved captures passed offline validation. Current
page layout and availability can change. The page exposes event IDs, while
selection IDs and bookmaker update times remain unknown. Records identify the
published rule profile and its overtime treatment, with applicability to
individual quotes still unverified.

Choose a stable, non-sensitive `--device` label for each machine sharing an archive.
It defaults to `local`. Raw evidence goes under `data/raw/`. Use `--archive` and
`--database` to override `archive/` and `data/local/odds.sqlite`.

The collector checks the HTML hash, independently verifies the parsed moneylines
against rendered text, and validates the normalized observations before publishing
the batch. The JSON report on stdout includes each source's status, snapshot path,
published manifest when available, observation counts, and parser issues. The
archived observation count includes all accepted statuses. The verified pregame
count covers future events with a resolved start time and two available prices.
Settlement rules remain unverified.

Exit code 0 means every selected source was archived and imported. Review any
parser issues for excluded markets. Exit code 1 means a source failed, with the
stage and reason in its report. Other sources can still finish. A capture that
fails validation leaves its evidence available for inspection. An interruption
before manifest publication can leave an orphan batch, which ingestion ignores.
If SQLite import fails after the completion manifest is written, the verified
archive remains available for retry with `history.py ingest` and the same paths.
Collection imports each newly published batch individually. Use the offline
ingest command below to import the rest of an archive.

## Standalone snapshot results

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

`config/settlement_rules.json` records separate reviewed rule-page profiles. Both
books include overtime for standard full-game NBA moneylines, but interruption
and void conditions differ. These profiles do not automatically assign rules to
existing quotes or approve cross-book comparisons.

Historical storage uses the v1 observation contract described in
`config/moneyline-v1.md` and enforced by `moneyline_contract.py`:

```sh
.venv/bin/python moneyline_contract.py tests/fixtures/moneyline-v1.json
.venv/bin/python -m unittest -v
```

The JSON fixture is synthetic. The small Pinnacle HTML fixture is a reduced
observed row for future parser work, not a live offer. Contract validation checks
structure, timestamps, outcome identity, and price consistency; it does not
certify EV eligibility. The collector continues to emit its source-specific JSONL.

## Offline history storage

Use the project Python environment from setup and run these commands from the
repository root. Substitute the folder of an existing DraftKings capture.
All history commands work offline.

```sh
.venv/bin/python history.py archive-snapshot data/YOUR_SNAPSHOT --device local
.venv/bin/python history.py ingest
.venv/bin/python history.py rebuild --database data/local/rebuilt.sqlite
.venv/bin/python history.py export > data/local/odds.csv
```

Archiving requires `metadata.json` with a matching HTML SHA-256 and UTC capture
times, `page.html`, and `visible.txt`. The converter reparses the saved HTML and
checks the price pairs against the saved rendered text. It leaves the original
snapshot files intact. Captures missing required evidence or failing verification
are rejected. Use a stable, non-sensitive device label. For snapshots outside
the repository, supply a portable repository-relative evidence reference with
`--evidence-path`. Review archive metadata before sharing it.

The converter writes one immutable JSONL batch under
`archive/odds/YYYY/MM/DD/draftkings/` and publishes its completion manifest under
`archive/runs/YYYY/MM/DD/` after the batch is ready. The archive filesystem must
support hard links for atomic publication. Repeating conversion with the same
metadata gives the same files. A later capture is retained even if its prices
are unchanged. A parser revision can point to the previous manifest for the
same capture with `--supersedes`, preserving both versions.

Ingestion checks manifests, SHA-256 hashes, row counts, IDs, and observation
schema before committing the imports and their ledger to SQLite. It ignores
batches without completion manifests and errors on corrupt or missing files
referenced by a manifest. Repeating ingestion skips already imported batches.
`rebuild` recreates the history rows in one transaction using only the contents
of an existing archive directory. With the default database path, this replaces
local history, including removing rows whose batches are absent from the
archive. If import fails, existing history is preserved. For recovery from a
damaged SQLite file or to compare databases, choose a new `--database` path.
Compare the printed counts and logical SHA-256 values for the imports and
observations tables across devices. An existing empty archive rebuilds to zero
rows. Use the global `--archive PATH` option before the subcommand to select an
alternate archive.

`export` writes UTF-8 CSV to standard output. It reads the selected SQLite database and emits one row per
selection, including unavailable sides and superseded or unreviewed records.
Treat source text and odds as text when importing into a spreadsheet. Save
exports, databases, and raw evidence under the Git-ignored `data/` directory.
Rechecking the original prices requires the original evidence.

### Synchronize history across devices

1. Finish or pause local collection and confirm a clean working tree with
   `git status --short`. Pull committed changes with `git pull --ff-only`,
   then run `.venv/bin/python history.py ingest`. Keep any unshared local
   captures if the pull fails.
2. Collect with a stable, non-sensitive device label. Before staging, run
   `.venv/bin/python history.py check-archive`. The check reports batch count,
   total bytes, and largest file size. It rejects missing pairs, invalid files,
   unexpected files or symlinks, source URLs with queries or fragments, and
   common credential patterns. It also stops at 10 MiB per file or 250 MiB for
   the archive so storage can be reviewed before adding more.
3. Review the exact manifest and JSONL contents for secrets and personal data.
   Automated checks are heuristic. Stage only the two reviewed files per batch
   with `git add -- <manifest-path> <batch-path>`, review `git diff --cached`,
   then commit and push those files. Keep local data, raw HTML, reports, and
   SQLite files out of the commit.
4. On the receiving checkout, pull with `git pull --ff-only`, then ingest or
   rebuild. If both devices committed distinct batches and the push is rejected,
   fetch and merge the other device's changes while retaining both archive
   pairs. Run the archive check and ingest again before pushing. If the same
   immutable path has different bytes, investigate the conflict before sharing.

Collection, archive checks, import, rebuild, and export are separate from Git
synchronization. The local handoff test uses independent directories and offline
captures. Physical second-device verification is pending.

SQLite's `observations` table retains legacy quotes with unresolved rules. The
`analysis_observations` view excludes quotes with unverified rules and linked
superseded observations. Further checks for event identity, rule applicability,
cross-book compatibility, and freshness are required for analysis.
