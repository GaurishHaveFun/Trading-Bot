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
