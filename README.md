# Bringing Down the House

Collect DraftKings NBA moneylines with clean team names, separate scores, explicit event status, and verified pregame output. Cross-book analysis and settlement-rule matching are future work.

## Setup and run

Requires Windows, Python 3.10 or newer, and Microsoft Edge. From this repository:

```powershell
.\setup.ps1
.\run.ps1
```

Setup installs pinned dependencies, including timezone data for Windows. Each run opens Edge and creates a timestamped folder under `data/`. If script execution is blocked, use:

```powershell
.\.venv\Scripts\python.exe scraper.py capture --method browser --channel msedge --headed --via-home
```

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

```powershell
.\.venv\Scripts\python.exe scraper.py parse data\YOUR_SNAPSHOT
.\.venv\Scripts\python.exe verify_snapshot.py data\YOUR_SNAPSHOT
.\.venv\Scripts\python.exe -m unittest -v
```

Rendered-text verification expects the observed six-price Spread/Total/Moneyline board. A changed layout or unavailable prices can cause verification to fail and withhold the pregame file. Collection remains on demand; access restrictions can interrupt it. Visible Edge mode is the tested collection path.

Capture time is not the sportsbook's last update time. Records are historical snapshots, not a guarantee that prices remain available. Game settlement rules are still unverified; these outputs are not yet suitable for cross-book arbitrage calculations.
