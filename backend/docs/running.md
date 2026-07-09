# Running the Screener

All commands are run from `backend/`.

## 1. One-time setup

```bash
cd backend
uv sync                  # installs deps into .venv (Python 3.12, pinned via .python-version)
cp .env.example .env     # fill in FINNHUB_API_KEY / ALPACA_KEY / ALPACA_SECRET later if needed — unused today
```

Nothing in `.env` is required to run — `yfinance` needs no API key. `ALERT_THRESHOLD` (default `0.70`) can be overridden here if you want alerts to fire more or less easily.

## 2. The four ways to fire it

### `--once` — single run, writes JSON + PDF

```bash
uv run python -m screener.main --once
```

Runs the full pipeline exactly once: builds the universe → fetches bars → scores every symbol → writes `output/runs/run_<UTC_TIMESTAMP>.json` and `output/reports/report_<UTC_TIMESTAMP>.pdf` → exits. This is what a cron/scheduled invocation actually calls once a day. See section 4 for details on the PDF report.

Console output is structured JSON logs (one line per event: `screener_start`, `fetching_bars`, `insufficient_bars` for skipped tickers, `run_written`, `screener_complete`). The `screener_complete` line summarizes the run:

```json
{"total_symbols": 18, "signals_evaluated": 15, "above_threshold": 0, "top5": ["SYF=0.56", ...], "output": "output/runs/run_..."}
```

`above_threshold` is how many symbols actually crossed `alert_threshold` and would count as a fired alert. It's normal for this to be `0` on any given day — the rule set is a strict multi-factor blend, not every ticker will line up on all 5 rules at once.

### `--ticker SYMBOL` — debug a single ticker

```bash
uv run python -m screener.main --ticker NVDA
```

Fetches and evaluates just that ticker and prints a readable per-rule breakdown to the console (score, which rules passed/failed, and the indicator values behind each rule). Does not write a JSON file, but does write a single-ticker PDF report to `output/reports/report_<TICKER>_<UTC>.pdf` (path printed after the breakdown). Use this to sanity-check why a specific stock did or didn't score well.

### `--backtest` — historical backtest of the current rules

```bash
uv run python -m screener.main --backtest
uv run python -m screener.main --backtest --days 30 --hold 5   # defaults shown explicitly
```

Answers a different question than `--once`/`--ticker`: not "what fires today," but "over the last N trading days, which watchlist stocks would have passed the rules, and would buying on the signal day have been profitable?" Fetches historical bars for the 16 symbols in `config/watchlist.yaml` (**not** the live `losers` universe — see `backend/docs/backtest.md` for why that universe isn't backtestable), walks each trading day in the trailing `--days` window (default 30) with no look-ahead, scores it against `config/rules.yaml`'s rules **minus `undervalued_pb`** (dropped because price-to-book has no historical series — see the caveats below), and for every day the score is at/above `ALERT_THRESHOLD`, simulates buying at that day's close and selling `--hold` trading days later (default 5).

Writes a standalone report to `output/reports/backtest_<UTC_ISO>.pdf` — a **separate file family** from `report_<UTC>.pdf`/`report_<TICKER>_<UTC>.pdf`; it never overwrites or merges with those. `--once` and `--ticker` behavior is completely unaffected by this mode. Prints a short console summary (signals fired, win rate, average return, and the delta vs. the baseline — see below) plus the PDF path.

The PDF opens with a caveats block (watchlist-only universe, dropped rule, fixed 5-day hold, no transaction costs, small sample) so the numbers aren't over-read, followed by an aggregate stats table and a full per-trade table (green for wins, red for losses). The key number is **signal average return vs. baseline** — the baseline is the average forward return across *every* evaluated symbol-day, signal or not, so the delta answers "did the rules actually add edge over just holding anything?" See `backend/docs/backtest.md` for the full design writeup.

May take up to ~60 seconds on a cold cache (16 symbols × ~500 calendar days of history); the `BarCache` warms it for subsequent runs.

### Bare — start the cron scheduler

```bash
uv run python -m screener.main
```

Starts an APScheduler loop that calls the same pipeline as `--once` on the cron schedule defined in `config/rules.yaml` (`schedule.on`, `schedule.timezone` — currently `0 16 * * 1-5` / `America/New_York`, i.e. 4pm ET on weekdays). Blocks until you send `SIGINT`/`SIGTERM` (Ctrl-C).

## 3. What's actually being screened right now

`config.py` defaults `universe` to `"losers"` (not the original static 10-symbol list). That means each run:

1. Pulls yfinance's `day_losers` screen, drops sub-$10B market cap / non-equity names, keeps the top 15 by % loss.
2. Unions that with any symbol in `config/watchlist.yaml` (big-tech/chip names) that's also down today.
3. Scores the combined set against `config/rules.yaml`'s current 5 rules (`big_tech_or_chip`, `oversold_band`, `quality_uptrend`, `near_52w_low`, `undervalued_pb`) — see `PLAN.md` for the rationale.

To go back to the original fixed 10-ticker universe for a run, set `UNIVERSE=static` in `.env` (or export it) before running.

### `big_tech_or_chip` is sector-aware, not just a watchlist check

The condition is `in_watchlist or is_chip`. `in_watchlist` is membership in the curated `config/watchlist.yaml` list; `is_chip` is derived from the symbol's yfinance `industry` string (`"semiconductor" in industry.lower()`), so any semiconductor-industry stock passes this rule even if it isn't in the watchlist — e.g. SKYT (SkyWater Technology) passes via `is_chip` despite never being added to `watchlist.yaml`.

One caveat: `industry` is reliably present on `.info` lookups (used by `--ticker` and the watchlist-down-today check) but is often missing on `yf.screen("day_losers")` quotes. When it's missing, `is_chip` just evaluates to `False` and the symbol falls back to the `in_watchlist` half of the rule — no extra network call is made to backfill it.

## 4. Where output goes

Every `--once` run (and every scheduler tick) writes to `output/runs/run_<UTC_ISO>.json`, matching the locked schema in `CLAUDE.md`. Nothing is overwritten — each run gets its own timestamped file.

### Output artifacts

Each `--once` run (and scheduler tick) now also writes a human-readable PDF report to `output/reports/report_<UTC_ISO>.pdf` — a ranked summary table plus a full per-ticker rule breakdown, with all numbers formatted cleanly (no raw floats, humanized volume, `—` instead of `inf` for missing P/B). This is purely additive: the JSON schema and file are unchanged. `--ticker SYMBOL` also writes a single-ticker PDF to `output/reports/report_<TICKER>_<UTC_ISO>.pdf` in addition to its terminal breakdown, and prints a one-line pointer to the file (`[PDF] Report written to ...`). See `backend/docs/output.md` for the full `pdf_writer` reference.

Note: the PDF's displayed "Run timestamp" is shown in US Eastern time (`America/New_York`, auto-switching `EST`/`EDT`) for readability — e.g. `Jul 08, 2026 11:08:42 PM EDT`. This is a display-only conversion; the JSON file, `output/runs/`/`output/reports/` filenames, and everything internal remain UTC per the spec.

## 5. Known noise (not bugs)

- yfinance sometimes prints `possibly delisted; no price data found` to stderr during the cache's incremental head/tail fetch (e.g. asking for a single day just outside a ticker's existing cached range). It self-corrects on the next fetch — the run still completes and produces correct data.
- Tickers with fewer than 200 cached bars are logged as `insufficient_bars` and skipped — this is intentional (non-negotiable #5: never let a `Signal` carry NaN indicators from too little history).

## 6. Tests

```bash
uv run pytest                      # everything, including live-network integration tests
uv run pytest -m "not integration" # fast, no network — what CI/local dev should run day to day
```
