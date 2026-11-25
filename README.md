# Bringing Down the House

A Python research project exploring how sportsbook prices differ, how they change
over time, and whether those differences reveal potential market inefficiencies.
The focus is NBA basketball, starting with pregame moneylines before expanding to
spreads, totals, and player props.

## Approach

The project follows a data-first workflow:

1. **Collect** publicly displayed odds and retain timestamped source evidence.
2. **Validate** prices against rendered page text, keeping event status, schedules,
   and missing information explicit.
3. **Preserve history** in immutable JSONL batches and a rebuildable SQLite database.
4. **Compare equivalent markets** across sportsbooks, accounting for matching events,
   betting lines, settlement rules, and quote freshness.
5. **Evaluate candidate edges** using historical and forward-collected observations.

The planned analysis uses Pinnacle prices, adjusted to remove the bookmaker's
margin, as a reference for estimating fair probabilities and identifying potential
positive expected value (+EV). These are probability estimates, not known true
probabilities. Cross-book arbitrage is a later extension.

## Software

**Python** powers collection, validation, and analysis. **Playwright with Chromium**
captures browser-rendered pages, and **Beautiful Soup** parses their HTML.
**JSONL** preserves portable observations, **SQLite** supports local historical
queries, and Python's **unittest** framework checks data handling and failure cases.

## Current scope

From the repository root, `python run.py collect --sport nba` uses the configured
Python environment to capture DraftKings NBA moneylines, verify prices against
rendered text, archive the observations, and import them into SQLite. The command
reports the outcome and any issues for the selected source. Add
`--source pinnacle` for full-game pregame NBA moneylines from Pinnacle. The
adapter was verified offline against two saved public captures.

Use `python matching.py OBSERVATION_ID_A OBSERVATION_ID_B` to inspect event
identity, team outcomes, and settlement compatibility in local history. The
matcher quarantines ambiguous listings and suppresses comparisons with unknown or
different settlement rules. Pinnacle and DraftKings have different interruption
and void rules, so their quotes cannot pass the settlement check. The +EV engine
remains under development.

Use `history.py ingest` or `history.py rebuild` to query a shared archive locally
and `history.py export` to produce CSV. Devices exchange reviewed JSONL batches
and manifests through explicit Git synchronization.

This is a research system using free scraped sources. It does not place bets.
Website changes and access restrictions can interrupt collection, and historical
prices do not guarantee current availability or a profitable strategy.

See [INSTRUCTIONS.md](INSTRUCTIONS.md) for setup, commands, data outputs, and
validation details.
