# Output Module (`screener/output`)

## Overview

The output module serialises a completed `ScreenerRun` to disk as a JSON file, using the locked schema that Phase 3 consumes. It contains one public function (`write_run`) and two private helpers (`_serialise`, `_fmt`).

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
        "atr_14": 4.2
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
