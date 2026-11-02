# Bringing Down the House

A Python project for collecting sportsbook odds and, eventually, finding positive expected value (+EV) and arbitrage opportunities.

Currently, it scrapes NBA moneylines from DraftKings using Playwright and Beautiful Soup. Odds analysis is still to come.

## Setup

Requires Windows, Python 3.10.11 and Microsoft Edge. Run these commands from the repository folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
```

## Run

```powershell
.\run.ps1
```

This opens Edge, loads the NBA board and saves the results. If PowerShell blocks the script, run:

```powershell
.\.venv\Scripts\python.exe scraper.py capture --method browser --channel msedge --headed --via-home
```

## Results

Each run creates a timestamped folder under `data/`:

- `quotes.jsonl` — parsed games and moneyline odds, one JSON record per game.
- `page.html`, `page.png` and `visible.txt` — the captured page, screenshot and rendered text.
- `metadata.json` — capture time and source details.
- `report.json` — event count and parsing issues.

Prices are saved in their original American format and converted to decimal odds. The capture time records when we collected them, not when the sportsbook last updated them.

To reparse a saved page or check prices against its saved text, replace `YOUR_SNAPSHOT` with the folder name:

```powershell
.\.venv\Scripts\python.exe scraper.py parse data\YOUR_SNAPSHOT
.\.venv\Scripts\python.exe verify_snapshot.py data\YOUR_SNAPSHOT
```

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest -v
```

## Current limits

Only DraftKings NBA moneylines are supported. Game start times, live status and settlement rules aren't extracted yet. Collection runs on demand, and changes to the site or access restrictions can interrupt it.

The next step is extracting event times and status, then checking reliability across repeated captures before adding more sportsbooks and analysis.
