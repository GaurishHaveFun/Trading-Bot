# Stock Screener — End-to-End Overview

## What the screener does

The screener is a Python batch service that runs on a configurable cron schedule (or on demand via CLI flags). On each run it loads a universe of stock ticker symbols, fetches up to 375 calendar days of daily OHLCV bars for each symbol from Yahoo Finance (served from an SQLite cache on subsequent runs), computes technical indicators from those bars, evaluates a set of weighted rules against those indicators using a safe expression engine, and writes a single ranked JSON file to disk. Tickers with fewer than 200 bars are skipped entirely to guarantee that all indicator values are non-NaN. The JSON output is sorted by score descending and consumed by Phase 3 of the trading bot.

---

## Data flow

```
config/universe.yaml
        |
        v
  StaticUniverse          config/rules.yaml
  (get_symbols)                  |
        |                        v
        |                   RulesConfig
        |                   (load_rules_config)
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
  write_run()
  output/runs/run_<UTC>.json
```

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
| oversold_rsi | 2.0 | `rsi(14) < 35` |
| above_long_trend | 1.5 | `close > sma(200)` |
| golden_cross_state | 1.5 | `sma(50) > sma(200)` |
| volume_spike | 1.0 | `volume > sma_volume(20) * 1.5` |
| reasonable_volatility | 1.0 | `atr(14) / close < 0.05` |

The score for a ticker is the sum of weights of passing rules divided by the sum of all weights. A ticker scoring at or above `alert_threshold` (default `0.70`, configurable via `.env`) is considered an alert-level signal.
