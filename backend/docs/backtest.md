# Backtest Module (`screener/backtest`)

## Overview

The `backtest` package answers a question the live pipeline can't: *over the last N trading days, which watchlist stocks would have passed the current screener rules, and would buying on the signal day have been profitable?* It's a historical replay of the rule engine, not a live feature — it never touches `output/runs/run_<UTC>.json` (the locked Phase-3 schema) or the scheduler.

| File | Role |
|------|------|
| `engine.py` | `evaluate_symbol` (pure, offline-testable) + `run_backtest` (orchestration, network) |
| `__init__.py` | Public re-exports: `run_backtest`, `evaluate_symbol` |

Wired up via `main.py --backtest` (`--days`/`--hold` overrides), rendered to PDF by `output/pdf_writer.py:write_backtest_report`.

---

## Design constraints (why the backtest looks the way it does)

### Universe = watchlist only, not the live "losers" screen

The default live universe (`UNIVERSE=losers` in `.env`) calls `yfinance.screen("day_losers")`, which only ever returns *today's* biggest losers — there is no historical daily-losers series to reconstruct, so that universe cannot be backtested. The 16-symbol static watchlist (`config/watchlist.yaml`) has real historical daily bars for every symbol, so `run_backtest` always uses it, regardless of the `UNIVERSE` setting.

### `undervalued_pb` is dropped

Price-to-book (`price_to_book`) is a live-quote scalar pulled from the universe provider's `get_quotes()` — it has no historical daily series to replay. Rather than fake it, `run_backtest` filters it out before constructing the `RuleEngine`:

```python
filtered_rules = [r for r in rules_config.rules if r.name != "undervalued_pb"]
engine = RuleEngine(filtered_rules)
```

This leaves 4 rules — `big_tech_or_chip` (2.0), `oversold_band` (2.0), `quality_uptrend` (1.5), `near_52w_low` (1.0) — with a combined weight of **6.5** instead of the live pipeline's 8.0. `RuleEngine.score()` already computes `passed_weight / total_weight` of whatever rules it was constructed with (see `docs/rules.md`), so this rescaling is automatic — no new scoring math was needed. It does mean backtest scores and live scores are **not directly comparable** (a backtest score of 77% reflects 5.0/6.5, not 5.0/8.0); the PDF's caveats block calls this out explicitly.

### Fixed 5-trading-day hold

There's no simulated stop-loss, profit target, or trailing exit — every signal is a single, mechanical trade: buy at the signal day's close, sell at the close `holding_days` trading days later (default 5, overridable via `--hold`).

```
return_pct = (sell_close - buy_close) / buy_close * 100
```

### No look-ahead

The single correctness property the whole backtest depends on: when evaluating day `i` (0-indexed, ascending bars), only `bars[:i+1]` is ever passed into `RuleEngine.evaluate()`. Indicators computed for day `i` (SMA, RSI, 52-week low, etc.) never see bar `i+1` or later. This mirrors how the rules would actually have fired in real time on that day.

---

## `evaluate_symbol()` — the pure core

```python
evaluate_symbol(
    symbol: str,
    bars: list[Bar],
    engine: RuleEngine,
    watchlist: set[str],
    threshold: float,
    holding_days: int,
    eval_days: int,
) -> tuple[list[BacktestTrade], list[float]]
```

Takes one symbol's full ascending bar list (already fetched by the caller) and does no I/O — fully unit-testable offline (`tests/test_backtest.py`). Walks a window of valid evaluation days and, for each, decides whether a signal fires.

**A day `i` is a valid evaluation point when:**
- `i >= 199` (`_MIN_BARS - 1` — at least 200 trailing bars exist, same floor as the live pipeline's `_MIN_BARS` in `main.py`, so indicators are never computed on too little history), **and**
- `i + holding_days < len(bars)` (there's a full forward window to compute the exit price).

The last `eval_days` such valid indices are walked (trailing window — this is what `--days 30` controls). For each:

1. Compute the forward return for **every** valid day, signal or not: `(bars[i+holding_days].close - bars[i].close) / bars[i].close * 100`. This always gets appended to the second return value — the baseline series (see below).
2. Evaluate the rules with no look-ahead: `engine.evaluate(symbol, bars[:i+1], meta=None, watchlist=watchlist)`, then `engine.score(results)`. `meta=None` is safe here since none of the 4 backtest rules touch quote metadata (only the dropped `undervalued_pb` rule needs it).
3. If `score >= threshold`, build a `BacktestTrade` (buy close = `bars[i].close`, sell close = `bars[i+holding_days].close`, `win = return_pct > 0`) and append it to the trades list.

Returns `(trades, all_forward_returns)`.

---

## `run_backtest()` — orchestration

```python
run_backtest(days: int = 30, holding_days: int = 5) -> BacktestResult
```

1. Loads settings, `config/rules.yaml`, and `config/watchlist.yaml` via the same `screener.config` functions `main.py` already uses.
2. Builds the filtered 4-rule `RuleEngine` (see above).
3. Fetches bars for every watchlist symbol via `YFinanceProvider` + `BarCache` (same `_CACHE_PATH = .cache/bars.db` as the live pipeline — a warm cache from a prior `--once`/`--ticker`/`--backtest` run speeds this up), using a lookback of **500 calendar days** (comment in `engine.py` explains the math: ~250-260 trading days in 500 calendar days comfortably covers 200 trailing bars + up to ~30 eval days + a few holding days of forward window).
4. Mirrors `main.py:run_screener`'s concurrency pattern exactly: `asyncio.Semaphore(10)` + `asyncio.gather(..., return_exceptions=True)`. Symbols that error out or come back with fewer than 200 bars are logged (`structlog` `fetch_error`/`insufficient_bars` warnings, same event names as the live pipeline) and skipped — never raise.
5. Calls `evaluate_symbol(...)` per symbol, accumulating all trades and all forward returns.
6. Aggregates into a `BacktestResult`:
   - `win_rate = wins / total_signals` (`0.0` if no signals — never a division-by-zero NaN, per the project's "never NaN" non-negotiable)
   - `avg_return_pct` = mean of trade returns (equal-weighted)
   - `total_return_pct` = sum of trade returns
   - `best_trade_return_pct` / `worst_trade_return_pct` = max/min trade return (`0.0` if no trades)
   - `baseline_avg_return_pct` = mean of **all** accumulated forward returns across every symbol and every evaluated day, signal or not
   - `trades` sorted by `return_pct` descending
7. Closes the `BarCache` (`cache.close()`), same cleanup pattern as `run_screener`.

### The baseline comparison — "did the rules actually add edge?"

`baseline_avg_return_pct` is the average `holding_days`-forward return you'd get by picking *any* watchlist symbol on *any* evaluated day, regardless of whether the rules fired. Comparing it to `avg_return_pct` (the average return only on days the rules actually signaled) is the real test of whether the rule set is adding predictive value over just holding the watchlist blindly:

```
signal_vs_baseline_pp = avg_return_pct - baseline_avg_return_pct
```

A positive delta means signal days outperformed the "hold anything" baseline; a delta near zero or negative means the rules aren't adding much (or any) edge over the period tested. This number is surfaced prominently in both the CLI summary and the PDF (`Signal avg return vs. baseline: +X.XX pp`).

---

## Models (`screener/models.py`)

`BacktestTrade` and `BacktestResult` are **not** part of the locked `ScreenerRun`/`Signal`/`RuleResult` JSON schema — they're separate, additive Pydantic models with their own UTC `field_validator`s on every datetime field (same pattern as `Signal.timestamp`/`ScreenerRun.run_timestamp`).

- `BacktestTrade`: one simulated trade — `ticker`, `signal_date`, `score`, `rules_passed`, `rules_total`, `buy_close`, `sell_date`, `sell_close`, `return_pct`, `win`.
- `BacktestResult`: the full run's config echo (`universe`, `holding_days`, `alert_threshold`, `lookback_days`, `start_date`, `end_date`) plus aggregates (`total_signals`, `wins`, `losses`, `win_rate`, `avg_return_pct`, `total_return_pct`, `best_trade_return_pct`, `worst_trade_return_pct`, `baseline_avg_return_pct`) and the sorted `trades` list.

---

## Output: `write_backtest_report()` (`output/pdf_writer.py`)

```python
write_backtest_report(result: BacktestResult, output_dir: Path = _OUTPUT_DIR) -> Path
```

Writes `output/reports/backtest_<UTC_ISO>.pdf` — a distinct file family from `report_<UTC>.pdf`/`report_<TICKER>_<UTC>.pdf`, never merged with or overwriting them. Reuses `pdf_writer.py`'s existing formatting helpers (`_fmt_num`, `_fmt_pct`, `_fmt_ts` — Eastern time) and `TableStyle` patterns (`_rule_table`'s green/red pass-fail coloring is mirrored for win/loss rows). Sections, top to bottom:

1. **Caveats block** — watchlist-only universe, `undervalued_pb` dropped (4-rule/6.5-weight denominator, not 8.0), fixed 5-day hold, no transaction costs/slippage, small sample size.
2. **Summary table** — period, universe, holding days, alert threshold, total signals, wins/losses, win rate, avg/total/best/worst return, and the baseline avg forward return, followed by the highlighted `Signal avg return vs. baseline: ±X.XX pp` line (green if positive, red if negative).
3. **Trades table** — Ticker, Signal Date, Score, Buy Close, Sell Date, Sell Close, Return %, Result (WIN/LOSS), sorted by return descending as given. If no trades fired, shows "No signals fired in this window." instead of an empty table.

Logs `report_written` via the module's `structlog` logger, same event name and shape as `write_report`/`write_ticker_report`.

---

## CLI (`main.py`)

```bash
uv run python -m screener.main --backtest
uv run python -m screener.main --backtest --days 30 --hold 5
```

`--backtest` is mutually exclusive with `--once`/`--ticker` (same `argparse` group). `--days` (default 30) and `--hold` (default 5) are independent optional flags only meaningful with `--backtest`. The handler, `run_backtest_cli()`, is orchestration only: configures logging, awaits `run_backtest(days, holding_days)`, calls `write_backtest_report(result)`, and prints a short console summary (signals fired, win rate, avg return, baseline delta, PDF path) — no scoring or aggregation logic lives in `main.py` (non-negotiable #8).

---

## Testing

`tests/test_backtest.py` is fully offline (no network), so it runs under `pytest -m "not integration"`:

- A hand-crafted bar series where exactly one day's rule condition is known to pass → asserts exactly one `BacktestTrade` with an exact, hand-computed `return_pct` and correct `win` flag.
- A series that never crosses the threshold → zero trades, but a non-empty forward-returns list (the baseline is still populated).
- A spike placed inside the final `holding_days` bars → confirms it's structurally excluded (no forward window to compute a sell price), regardless of what the rule condition would say.
- `eval_days` windowing → confirms only the trailing N valid days are walked.
- A hand-built `BacktestResult` (bypassing `run_backtest`/network) fed into `write_backtest_report` → confirms the output file exists, is non-empty, and starts with the `%PDF` magic bytes, including both a winning and a losing trade to exercise the color-coding path.

Any live-network end-to-end test of `run_backtest` itself would be marked `@pytest.mark.integration` (see `tests/test_yfinance_provider.py` for the pattern) — none exists yet, since `evaluate_symbol`'s offline tests already cover the correctness-critical logic (no look-ahead, exact return math, windowing).
