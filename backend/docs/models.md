# models.py

Module: `src/screener/models.py`

All Pydantic v2 data models for the stock screener. Every model that carries a timestamp enforces UTC via a `@field_validator`.

---

## Models

### Ticker

Represents a single tradeable instrument in the universe.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `symbol` | `str` | required | Ticker symbol, e.g. `"AAPL"` |
| `name` | `str` | `""` | Full company name |
| `sector` | `str` | `""` | GICS sector or similar |
| `market_cap` | `float` | `0.0` | Market capitalisation in USD |

---

### Bar

A single OHLCV bar (one time-period of price data).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `timestamp` | `datetime` | required | Bar open time, always UTC |
| `open` | `float` | required | Opening price |
| `high` | `float` | required | Intraday high |
| `low` | `float` | required | Intraday low |
| `close` | `float` | required | Closing price |
| `volume` | `int` | required | Total shares traded |

**UTC enforcement:** `must_be_utc` validator converts naive datetimes to UTC and normalises tz-aware datetimes to UTC via `astimezone`.

---

### RuleResult

The outcome of evaluating one weighted rule against a ticker's indicators.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `rule_name` | `str` | required | Identifier matching a rule in `rules.yaml` |
| `passed` | `bool` | required | Whether the condition evaluated to `True` |
| `weight` | `float` | required | Rule weight from `rules.yaml` |
| `detail` | `dict[str, Any]` | `{}` | Key indicator values used during evaluation |

---

### Signal

The aggregated screening result for one ticker at one point in time.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `ticker` | `str` | required | Ticker symbol |
| `timestamp` | `datetime` | required | Evaluation time, always UTC |
| `score` | `float` | required | Weighted score in `[0.0, 1.0]` |
| `rules_passed` | `int` | required | Number of rules that passed |
| `rules_total` | `int` | required | Total number of rules evaluated |
| `rule_results` | `list[RuleResult]` | required | Per-rule breakdown |
| `snapshot` | `dict[str, Any]` | `{}` | Indicator values at evaluation time |

**Validators:**
- `must_be_utc` — same UTC normalisation as `Bar`.
- `score_in_range` — raises `ValueError` if `score` is outside `[0.0, 1.0]`.

---

### ScreenerRun

Top-level envelope written to the JSON output file. This schema is consumed by Phase 3 and must not change.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `run_timestamp` | `datetime` | required | UTC time when the screener run started |
| `universe` | `str` | required | Universe identifier, e.g. `"static"` |
| `alert_threshold` | `float` | `0.70` | Minimum score to consider a ticker an alert |
| `signals` | `list[Signal]` | `[]` | All evaluated ticker signals |

**UTC enforcement:** same `must_be_utc` validator as `Bar` and `Signal`.

---

### FundamentalsSnapshot

Point-in-time snapshot of the 6 balance-sheet-quality metrics for a ticker, computed by `FundamentalsProvider` (see `docs/data.md`). Used to hard-exclude weak-balance-sheet tickers before they reach the rule engine.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `ticker` | `str` | required | Ticker symbol |
| `as_of` | `datetime` | required | When the snapshot was computed, always UTC |
| `years_available` | `int` | required | Number of usable annual reporting periods found |
| `fcf_5y_cumulative` | `float \| None` | required | Cumulative free cash flow across available years |
| `interest_coverage` | `float \| None` | required | Latest-year EBIT / \|interest expense\| |
| `gross_margin` | `float \| None` | required | Average gross margin across available years |
| `ocf_ni_ratio` | `float \| None` | required | Average operating-cash-flow / net-income ratio |
| `net_margin` | `float \| None` | required | Average net margin across available years |
| `share_dilution_5y` | `float \| None` | required | Share-count growth over available history |

**UTC enforcement:** `must_be_utc` validator on `as_of`, same pattern as `Bar`.

Any metric that could not be computed (missing row, insufficient history, etc.) is `None` rather than `NaN` — see `docs/data.md` for exactly what `None` means per metric.

---

### QualityGateResult

The outcome of running `evaluate_quality_gate()` (see `docs/rules.md`) against a `FundamentalsSnapshot`.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `ticker` | `str` | required | Ticker symbol |
| `passed` | `bool` | required | `True` only if zero checks failed |
| `failed_metrics` | `list[str]` | `[]` | Names of metrics that failed their threshold check |
| `detail` | `dict[str, Any]` | `{}` | Metric values and thresholds for every metric that was checked |

**Not part of the locked output schema:** `QualityGateResult` is an internal-only type. It is never added to `Signal` or `ScreenerRun` and never serialized to the JSON output file — a ticker that fails the gate is simply dropped in `main.py` before a `Signal` is ever created for it, so the locked `ScreenerRun` JSON schema (documented in `CLAUDE.md`) is unchanged by this feature. Exclusions are only visible via structlog warnings.

---

### BacktestTrade

A single simulated trade from the historical backtest: buy at the signal day's close, sell `holding_days` trading days later.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `ticker` | `str` | required | Ticker symbol |
| `signal_date` | `datetime` | required | Date the signal fired (buy date), always UTC |
| `score` | `float` | required | Signal score at trade entry |
| `rules_passed` | `int` | required | Number of rules that passed on the signal |
| `rules_total` | `int` | required | Total number of rules evaluated on the signal |
| `buy_close` | `float` | required | Close price on `signal_date` (entry price) |
| `sell_date` | `datetime` | required | Date the trade was closed (`holding_days` trading days after `signal_date`), always UTC |
| `sell_close` | `float` | required | Close price on `sell_date` (exit price) |
| `return_pct` | `float` | required | Trade return: `(sell_close - buy_close) / buy_close` |
| `win` | `bool` | required | `True` if `return_pct > 0` |

**UTC enforcement:** `must_be_utc` validator on `signal_date` and `sell_date`, same pattern as `Bar`.

---

### BacktestResult

Aggregate output of a historical backtest run, produced by `backtest/engine.py` (see `docs/data.md`).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `universe` | `str` | required | Universe identifier used for the backtest |
| `holding_days` | `int` | required | Trading days held per simulated trade |
| `alert_threshold` | `float` | required | Minimum score used to trigger a simulated trade |
| `lookback_days` | `int` | required | Number of historical days scanned for signals |
| `start_date` | `datetime` | required | Start of the backtest window, always UTC |
| `end_date` | `datetime` | required | End of the backtest window, always UTC |
| `total_signals` | `int` | required | Total number of simulated trades generated |
| `wins` | `int` | required | Number of trades with `return_pct > 0` |
| `losses` | `int` | required | Number of trades with `return_pct <= 0` |
| `win_rate` | `float` | required | `wins / total_signals`, in `[0.0, 1.0]` |
| `avg_return_pct` | `float` | required | Mean `return_pct` across all trades |
| `total_return_pct` | `float` | required | Summed `return_pct` across all trades |
| `best_trade_return_pct` | `float` | required | Highest single-trade `return_pct` |
| `worst_trade_return_pct` | `float` | required | Lowest single-trade `return_pct` |
| `baseline_avg_return_pct` | `float` | required | Mean buy-and-hold return over the same window, for comparison |
| `trades` | `list[BacktestTrade]` | `[]` | Every simulated trade in the backtest |

**UTC enforcement:** `must_be_utc` validator on `start_date` and `end_date`, same pattern as `Bar`.

**Not part of the locked output schema:** `BacktestResult` and `BacktestTrade` are separate, additive models produced by historical backtest mode — they do not appear in the locked `ScreenerRun` JSON schema documented in `CLAUDE.md`.

---

## Pipeline Flow

```
yfinance / cache
      |
      v
  list[Bar]           ← BarCache.get / put (data layer)
      |
      v
  indicators          ← indicators/library.py computes sma, ema, rsi, atr, …
      |
      v
  RuleResult(s)       ← rules/engine.py evaluates each condition via asteval
      |
      v
  Signal              ← one per ticker, aggregates score + rule_results
      |
      v
  ScreenerRun         ← one per screener run, serialised to JSON by output/json_writer.py
```

All intermediate data is held in-memory as plain Pydantic objects. The only I/O boundary is `ScreenerRun` → JSON file (written by `output/json_writer.py`).
