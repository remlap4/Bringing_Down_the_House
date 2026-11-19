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

DraftKings NBA moneyline collection, price verification, and offline historical
storage are implemented. Other sportsbook displays have been explored, but
Pinnacle collection, cross-book matching, and the +EV analysis engine remain under
development.

This is a research system using free scraped sources. It does not place bets.
Website changes and access restrictions can interrupt collection, and historical
prices do not guarantee current availability or a profitable strategy.

See [INSTRUCTIONS.md](INSTRUCTIONS.md) for setup, commands, data outputs, and
validation details.
