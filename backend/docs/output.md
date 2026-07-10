# Output Module (`screener/output`)

## Overview

The output module renders a completed `ScreenerRun` to disk in two forms:

- **JSON** (`json_writer.py`) — the locked, machine-readable schema that Phase 3 consumes. Public function: `write_run`.
- **PDF** (`pdf_writer.py`) — a purely additive, human-readable report built with `reportlab`. Public functions: `write_report` (full multi-ticker report), `write_ticker_report` (single-ticker report for `--ticker` debug mode), and `write_backtest_report` (historical backtest report for `--backtest` mode).

The JSON schema is never touched by the PDF writer — both are rendered independently from the same `ScreenerRun`/`Signal` Pydantic models, so the PDF can evolve freely without risking the Phase 3 contract.

---

## `write_run(run, output_dir)`

**Signature:** `write_run(run: ScreenerRun, output_dir: Path = Path("output/runs")) -> Path`

Writes a `ScreenerRun` to a timestamped JSON file and returns the `Path` of the file written.

### Output directory

The default directory is `output/runs/` relative to wherever the process is launched (i.e. `backend/output/runs/`). The directory is created automatically with `mkdir(parents=True, exist_ok=True)` if it does not already exist. A custom `output_dir` can be passed — used by tests via `tmp_path`.

### Filename pattern

```
run_<UTC_ISO>.json
```

The timestamp component is derived from `run.run_timestamp` formatted as `%Y%m%dT%H%M%SZ`, for example:

```
run_20260627T200114Z.json
```

Running the screener twice in the same second would overwrite the earlier file; in practice the scheduler fires at most once per minute so collisions do not occur.

### Logging

After writing, the function emits a structured log event at INFO level:

```json
{"event": "run_written", "path": "output/runs/run_20260627T200114Z.json", "signals": 10}
```

---

## `_serialise(run)`

**Signature:** `_serialise(run: ScreenerRun) -> dict`

Converts a `ScreenerRun` Pydantic model to the locked Phase-3 JSON schema dict. This is the single place that owns the mapping from internal models to the wire format — if the schema ever needs to change, this is the only function to edit.

### Mapping: ScreenerRun → JSON

| JSON key | Source |
|---|---|
| `run_timestamp` | `run.run_timestamp` formatted via `_fmt()` |
| `universe` | `run.universe` (string, e.g. `"static"`) |
| `alert_threshold` | `run.alert_threshold` (float, e.g. `0.70`) |
| `signals` | list of serialised `Signal` objects |

### Mapping: Signal → JSON

| JSON key | Source |
|---|---|
| `ticker` | `signal.ticker` |
| `timestamp` | `signal.timestamp` formatted via `_fmt()` |
| `score` | `round(signal.score, 4)` |
| `rules_passed` | `signal.rules_passed` |
| `rules_total` | `signal.rules_total` |
| `snapshot` | `signal.snapshot` (dict, passed through as-is) |
| `rule_results` | list of serialised `RuleResult` objects |

### Mapping: RuleResult → JSON

| JSON key | Source |
|---|---|
| `rule_name` | `r.rule_name` |
| `passed` | `r.passed` (bool) |
| `weight` | `r.weight` (float) |
| `detail` | `r.detail` (dict, passed through as-is) |

---

## `_fmt(dt)`

**Signature:** `_fmt(dt: datetime) -> str`

Formats any `datetime` as a UTC ISO 8601 string with a `Z` suffix, e.g. `"2026-06-27T20:01:14Z"`.

### Why the `Z` suffix?

The `Z` suffix is the standard ISO 8601 designator for UTC (`+00:00`). It is preferred over `+00:00` for compactness and is unambiguous for all consumers. The function calls `.astimezone(timezone.utc)` before formatting, so timezone-aware datetimes in any zone are correctly normalised before output. All internal `datetime` objects in the screener already carry UTC timezone info (enforced by Pydantic validators in `Bar`, `Signal`, and `ScreenerRun`), so `_fmt` is a defensive final conversion step.

---

## Full JSON Schema (locked — do not deviate)

```json
{
  "run_timestamp": "2026-06-27T20:01:14Z",
  "universe": "static",
  "alert_threshold": 0.70,
  "signals": [
    {
      "ticker": "AAPL",
      "timestamp": "2026-06-27T20:00:00Z",
      "score": 0.8571,
      "rules_passed": 3,
      "rules_total": 5,
      "snapshot": {
        "close": 192.31,
        "volume": 54000000,
        "rsi_14": 31.2,
        "sma_50": 188.4,
        "sma_200": 175.1,
        "atr_14": 4.2,
        "price_to_book": 3.42,
        "change_pct": -1.25,
        "in_watchlist": true,
        "industry": "Consumer Electronics",
        "is_chip": false
      },
      "rule_results": [
        {
          "rule_name": "oversold_rsi",
          "passed": true,
          "weight": 2.0,
          "detail": {"rsi_14": 31.2, "threshold": 35}
        }
      ]
    }
  ]
}
```

---

## `pdf_writer.py` — human-readable PDF report

Renders the same `ScreenerRun`/`Signal` models to a styled PDF using reportlab's `platypus` API (`SimpleDocTemplate`, `Table`, `TableStyle`, `Paragraph`, `Spacer`). This exists because the locked JSON schema above is great for Phase 3 but hard for a human to scan; the PDF is a second, additive artifact — it never changes what `write_run` writes.

### `write_report(run, output_dir, rule_descriptions=None)`

**Signature:** `write_report(run: ScreenerRun, output_dir: Path = Path("output/reports"), rule_descriptions: dict[str, str] | None = None) -> Path`

Writes the full multi-ticker report to `output/reports/report_<UTC_ISO>.pdf`, using the same `run.run_timestamp.strftime("%Y%m%dT%H%M%SZ")` naming convention as `json_writer.write_run`. Contents, top to bottom:

1. **Header block** — title, run timestamp (formatted for display via `_fmt_ts` — see Formatting helpers below), universe, alert threshold, and signal counts (total / at-or-above threshold).
2. **"How to Read This Report" block** (only rendered when `rule_descriptions` is passed and non-empty) — a plain-English glossary (`_GLOSSARY`: Score, Rules passed, Close, Volume, Change %, RSI-14, SMA-50/SMA-200, ATR-14, P/B) followed by a "What each check means" section pairing each rule's humanized name with its `description` string sourced from `config/rules.yaml` (threaded through by `main.py`).
3. **Ranked summary table** — one row per signal in the order given (already score-descending from `main.py:run_screener`): Rank, Ticker, Score %, Rules (`4/5`), Watchlist (Yes/No), Close, Change %, RSI-14, SMA-200, P/B. Rows scoring at/above `alert_threshold` are shaded and bolded via `TableStyle`.
4. **Per-ticker sections** — one per signal, each with a heading (`TICKER — 81.00% — 4/5 rules`), a one-line formatted snapshot, an optional plain-English takeaway (see below), and a full rule breakdown table (Rule / Pass ✓·✗ / Weight / Detail).

When `rule_descriptions` is `None` or `{}` (the default), none of the new blocks are rendered and output is unchanged from before this feature — existing callers that don't pass the argument see byte-identical behavior.

Logs `report_written` (path, signal count) via the module's `structlog` logger, same pattern as `json_writer.write_run`'s `run_written` event.

### `write_ticker_report(signal, alert_threshold, universe, output_dir, rule_descriptions=None)`

**Signature:** `write_ticker_report(signal: Signal, alert_threshold: float, universe: str, output_dir: Path = Path("output/reports"), rule_descriptions: dict[str, str] | None = None) -> Path`

Single-ticker version used by `--ticker` debug mode. Writes `output/reports/report_<TICKER>_<UTC_ISO>.pdf` (timestamp taken at call time, since a lone `Signal` carries no run timestamp). Internally wraps the signal in a throwaway `ScreenerRun` and calls the same `_header_block`/`_ticker_section` helpers as `write_report`, so the per-ticker layout has one source of truth shared by both entry points — no duplicated rendering logic. Same optional `rule_descriptions` behavior as `write_report`: passing it adds the "How to Read This Report" block and the per-ticker plain-English takeaway; omitting it leaves the report unchanged.

### `write_backtest_report(result, output_dir)`

**Signature:** `write_backtest_report(result: BacktestResult, output_dir: Path = Path("output/reports")) -> Path`

Renders a `BacktestResult` — not a `ScreenerRun` — used by `--backtest` mode. This is a separate report family from `write_report`/`write_ticker_report`, with its own top-level layout (no `_header_block`, no summary/per-ticker tables). Writes `output/reports/backtest_<UTC_ISO>.pdf`, with the timestamp taken at write time (a `BacktestResult` carries no single run timestamp) using the same `%Y%m%dT%H%M%SZ` convention as the other two writers. Contents, top to bottom:

1. **Title** — "Screener Backtest Report".
2. **Caveats block** (`_backtest_caveats`) — a plain-language disclaimer covering: the fixed 16-symbol watchlist universe (`config/watchlist.yaml`, not the live day-losers screen), the `undervalued_pb` rule being dropped from scoring (no historical daily price-to-book series), the fixed N-trading-day hold with no stop-loss/profit-target/transaction-cost modeling, and a small-sample-size caveat.
3. **Summary stats table** (`_backtest_stats`) — period, universe, holding period, alert threshold, total signals, wins/losses, win rate, avg return per trade, total return (equal-weight), best/worst trade, and baseline avg forward return across all symbol-days, followed by a colored "Signal avg return vs. baseline" delta line (green if ≥ 0, red if negative).
4. **Trade table** (`_trade_table`) — one row per simulated trade (ticker, signal date, score, buy close, sell date, sell close, return %, WIN/LOSS), sorted as given (already return-descending from `run_backtest`); WIN/LOSS cells are colored green/red, matching `_rule_table`'s pass/fail coloring. Renders a "No signals fired in this window." message instead if `result.trades` is empty.

Logs `report_written` (path, `result.total_signals`) via the module's `structlog` logger, same pattern as `write_report` and `write_ticker_report`.

### Plain-English additions (`_how_to_read_block`, `_plain_english_takeaway`)

Two purely additive helpers, both gated on a truthy `rule_descriptions: dict[str, str]` (rule name → description, sourced from `config/rules.yaml`'s `description:` field and threaded through by `main.py` as `{r.name: r.description for r in rules_config.rules}`):

- **`_how_to_read_block(rule_descriptions)`** — the glossary + per-rule explanation block described above, inserted once near the top of the report (after the header, before the summary table).
- **`_plain_english_takeaway(signal, alert_threshold, rule_descriptions)`** — a short per-ticker verdict inserted into `_ticker_section` above the numeric rule table: "Strong match" (score ≥ `alert_threshold`), "Partial match" (score ≥ 0.5), or "Weak match", plus "cleared X of Y checks", and two lines listing which rules (by humanized name) were met (✓) and missed (✗).

Neither function touches the numeric tables (`_summary_table`, `_rule_table`) or any model — they only add `Paragraph`/`Spacer` flowables built from data already on `Signal`/`ScreenerRun` plus the caller-supplied descriptions.

### Formatting helpers

The raw floats in the JSON snapshot (e.g. `540.8800048828125`) are exactly what makes the terminal/JSON output hard to read; these helpers are the actual fix, applied only at PDF render time (the JSON values are untouched):

| Helper | Example |
|---|---|
| `_fmt_num(v, decimals=2)` | `540.8800048828125 → "540.88"`; `None`/`inf`/`NaN` → `"—"` |
| `_fmt_pb(v)` | Price-to-book specific alias of `_fmt_num` — `inf` (no data available) renders as `"—"`, never `"inf"` |
| `_fmt_pct(v, decimals=2)` | `v` already in percentage units → `-6.891234 → "-6.89%"` |
| `_fmt_vol(v)` | Humanized volume → `27_754_556 → "27.75M"`; also handles `K`/`B` and sub-1000 values |
| `_fmt_ts(dt)` | Display-only conversion to US Eastern time (auto EST/EDT via `zoneinfo`), 12-hour clock — `2026-06-27T20:00:00Z → "Jun 27, 2026 04:00:00 PM EDT"`. Internal storage/JSON stay UTC per spec; this only affects the PDF's rendered text. |

### Wiring (`main.py`)

- `run_screener()` calls `write_report(run)` right after `write_run(run)` and adds `report=str(report_path)` to the `screener_complete` log event. The JSON write path is untouched.
- `run_ticker_debug()` keeps `_print_ticker_breakdown(...)` for the terminal table, then calls `write_ticker_report(signal, settings.alert_threshold, settings.universe)` and prints a one-line pointer: `[PDF] Report written to output/reports/report_<TICKER>_<UTC>.pdf`.
- `run_backtest_cli(days, holding_days)` (`--backtest` mode) calls `run_backtest(...)` to get a `BacktestResult`, then `write_backtest_report(result)`, then prints a short console summary (signals fired, win rate, avg return, baseline, and a final `[PDF] Report written to ...` pointer).

Both functions are orchestration-only calls into the `output` package — no rendering logic lives in `main.py` (non-negotiable #8).

### Dependency

`reportlab>=4.2` (pure-Python, no system libraries required) — added to `pyproject.toml`.
