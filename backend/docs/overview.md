# Stock Screener — End-to-End Overview

## What the screener does

The screener is a Python batch service that runs on a configurable cron schedule (or on demand via CLI flags). On each run it loads a universe of stock ticker symbols, fetches up to 375 calendar days of daily OHLCV bars for each symbol from Yahoo Finance (served from an SQLite cache on subsequent runs), computes technical indicators from those bars, evaluates a set of weighted rules against those indicators using a safe expression engine, and writes a ranked JSON file plus a human-readable PDF report to disk. Tickers with fewer than 200 bars are skipped entirely to guarantee that all indicator values are non-NaN. The JSON output is sorted by score descending and consumed by Phase 3 of the trading bot; the PDF (`write_report()`, `docs/output.md`) is a human-facing artifact rendered from the same data.

---

## Data flow

```
config/universe.yaml   config/watchlist.yaml     config/rules.yaml
        |                       |                        |
        v                       v                        v
  StaticUniverse          load_watchlist()           RulesConfig
  (get_symbols)                 |                  (load_rules_config)
        |                       v
        |                 LosersUniverse (get_symbols)
        |                 yfinance day_losers screen,
        |                 unioned with watchlist
        |                 tickers currently down today
        |                       |
        +-----------------------+
     _build_universe(settings.universe) picks one
     provider — "losers" is the default (settings.universe
     defaults to "losers"); "static" selects the other
                       |
                       v
             symbols  (+ get_quotes() metadata)
                       |
                       v
  YFinanceProvider  <-->  BarCache (SQLite)
  (get_bars, async)       (.cache/bars.db)
        |
        v
  list[Bar]  (>= 200 bars, or ticker skipped)
        |
        v
  pandas DataFrame
        |
        +----> indicators/library.py
        |      (sma, ema, rsi, atr, sma_volume,
        |       macd_line, macd_signal_line, macd_histogram,
        |       latest_close, latest_volume)
        |
        v
  RuleEngine.evaluate()
  (asteval — safe expression evaluation)
        |
        v
  list[RuleResult]  +  score (weighted ratio)
        |
        v
  Signal (ticker, timestamp, score, snapshot, rule_results)
        |
        v
  ScreenerRun (sorted by score desc)
        |
        v
  write_run()                     write_report()
  output/runs/run_<UTC>.json      output/reports/report_<UTC>.pdf
```

In parallel with bar fetching, `FundamentalsProvider.get_fundamentals()` also fetches a `FundamentalsSnapshot` per symbol (cached daily in `.cache/fundamentals.db` — see `docs/data.md`). Each snapshot is passed through `evaluate_quality_gate()` (`docs/rules.md`) immediately after the `>= 200 bars` check; a ticker that fails the gate is dropped there and never reaches `RuleEngine.evaluate()` or produces a `Signal`.

`LosersUniverse` (`docs/universe.md`) is the default universe provider — it screens yfinance's `day_losers` query down to large-cap (>= $10B market cap) equities, ranks by % loss, and unions the top 15 with any `config/watchlist.yaml` symbols that are also down today. `StaticUniverse` (backed by `config/universe.yaml`) is the fallback when `UNIVERSE=static` is set.

---

## CLI modes

All commands are run from the `backend/` directory.

### `--once`

```bash
uv run python -m screener.main --once
```

Runs the full pipeline exactly once across the entire universe, writes `output/runs/run_<UTC>.json`, then exits. This is the primary mode for scheduled invocations and manual one-shot runs.

### `--ticker SYMBOL`

```bash
uv run python -m screener.main --ticker AAPL
```

Fetches and evaluates a single ticker and prints a human-readable per-rule breakdown to stdout. Does not write a JSON file. Intended for debugging individual tickers and validating rule logic.

Example output:
```
============================================================
  AAPL  |  score: 57.14%  |  2/5 rules passed
============================================================
  Snapshot: {'close': 192.31, 'volume': 54000000, ...}
  Rule                      Pass   Weight  Detail
  -------------------------------------------------------
  oversold_rsi               ✓      2.0  {'rsi_14': 31.2}
  above_long_trend           ✗      1.5  {}
  ...
```

### `--backtest`

```bash
uv run python -m screener.main --backtest
uv run python -m screener.main --backtest --days 30 --hold 5
```

Runs a historical backtest of the current rules over the watchlist (`screener.backtest.run_backtest`, `docs/backtest.md`): walks the trailing `--days` trading days (default `30`), evaluates the rules on each valid day, and simulates a fixed `--hold`-trading-day hold (default `5`) per signal — buy at the signal day's close, sell at the close `--hold` bars later. Prints a console summary (signals fired, win rate, average return, and a baseline comparison) and writes `output/reports/backtest_<UTC>.pdf` via `write_backtest_report()`. Does not write a JSON file.

### Bare (cron scheduler)

```bash
uv run python -m screener.main
```

Starts the APScheduler cron loop (implemented in Step 9 — `screener.scheduler`). The cron expression and timezone are read from `config/rules.yaml` under the `schedule` key. The scheduler calls `run_screener()` on each tick.

---

## Config files

### `config/universe.yaml`

Defines the list of ticker symbols to screen. The `StaticUniverse` class reads this file and returns the list of strings via `get_symbols()`. The default universe contains 10 symbols.

### `config/rules.yaml`

Defines two things:

1. **`schedule`** — cron expression (`on`) and timezone used by APScheduler.
2. **`rules`** — a list of named, weighted rule conditions evaluated by `RuleEngine`.

Each rule has:
- `name` — identifier used in JSON output and logs
- `weight` — contribution to the overall score (higher = more important)
- `condition` — an expression string evaluated by `asteval` against a symbol table of indicator values

Current rules:

| Name | Weight | Condition |
|---|---|---|
| big_tech_or_chip | 2.0 | `in_watchlist or is_chip` |
| oversold_band | 2.0 | `rsi(14) > 25 and rsi(14) < 40` |
| quality_uptrend | 1.5 | `close > sma(200)` |
| medium_term_momentum | 1.0 | `sma(50) > sma(100)` |
| macd_bullish | 1.0 | `macd_line() > macd_signal_line()` |
| near_52w_low | 0.5 | `close <= low_52w(252) * 1.15` |
| undervalued_pb | 1.5 | `price_to_book < 4` |

The score for a ticker is the sum of weights of passing rules divided by the sum of all weights. A ticker scoring at or above `alert_threshold` (default `0.70`, configurable via `.env`) is considered an alert-level signal.

### `config/quality_screen.yaml`

Defines the 6 fixed thresholds used by the fundamentals quality gate (`evaluate_quality_gate()`, see `docs/rules.md`) to hard-exclude tickers with weak balance-sheet fundamentals before rule evaluation. Unlike `rules.yaml`'s weighted scoring, this is a pass/fail filter — a ticker failing any one check (on a metric it has data for) is dropped entirely.

| Metric | Threshold | Fails if |
|---|---|---|
| fcf_5y_cumulative | `min_fcf_5y_cumulative` = 0.0 | `< 0.0` |
| interest_coverage | `min_interest_coverage` = 2.0 | `< 2.0` |
| gross_margin | `min_gross_margin` = 0.15 | `< 0.15` |
| ocf_ni_ratio | `min_ocf_ni_ratio` = 0.7 | `< 0.7` |
| net_margin | `min_net_margin` = 0.05 | `< 0.05` |
| share_dilution_5y | `max_share_dilution_5y` = 0.20 | `> 0.20` |
