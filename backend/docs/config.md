# config.py

Module: `src/screener/config.py`

Handles all configuration loading: environment variables via pydantic-settings and the rules/schedule YAML via Pydantic models.

---

## Settings

`Settings` extends `pydantic_settings.BaseSettings`. On instantiation it reads values from the `.env` file (path: `backend/.env`) and falls back to the defaults listed below. Unknown environment variables are silently ignored (`extra="ignore"`).

| Field | Type | Default | Env var |
|-------|------|---------|---------|
| `finnhub_api_key` | `str` | `""` | `FINNHUB_API_KEY` |
| `alpaca_key` | `str` | `""` | `ALPACA_KEY` |
| `alpaca_secret` | `str` | `""` | `ALPACA_SECRET` |
| `alert_threshold` | `float` | `0.70` | `ALERT_THRESHOLD` |
| `log_level` | `str` | `"INFO"` | `LOG_LEVEL` |
| `universe` | `str` | `"losers"` | `UNIVERSE` |
| `watchlist_path` | `str` | `"config/watchlist.yaml"` | `WATCHLIST_PATH` |

The API key fields are stubs for Phase 2+ providers (Finnhub, Alpaca). They are intentionally empty strings in Phase 1 because only yfinance is used.

`universe` selects which `UniverseProvider` a run uses — `"losers"` (the current default, yfinance's `day_losers` screen unioned with `config/watchlist.yaml`) or `"static"` (the original fixed 10-symbol list in `config/universe.yaml`). `watchlist_path` points `load_watchlist()` at the YAML file listing the curated big-tech/chip symbols consulted by both the `"losers"` universe and the `big_tech_or_chip` rule.

### get_settings() — singleton accessor

```python
from screener.config import get_settings

settings = get_settings()
print(settings.alert_threshold)  # 0.70
```

`get_settings()` constructs the `Settings` object once and caches it in the module-level `_settings` variable. All library code that needs configuration should call `get_settings()` rather than instantiating `Settings` directly, so that test code can reset the singleton if needed.

---

## Rules Config Models

These models represent the parsed `config/rules.yaml` file.

### RuleConfig

One entry in the `rules:` list.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Unique rule identifier |
| `weight` | `float` | Contribution weight for the composite score |
| `condition` | `str` | asteval expression string evaluated by the rules engine |
| `description` | `str = ""` | Plain-English explanation of the rule, shown in the PDF report's "how to read this" and takeaway blocks (`output/pdf_writer.py`'s `_how_to_read_block` and `_plain_english_takeaway`, fed via `main.py`'s `rule_descriptions={r.name: r.description for r in rules_config.rules}`); defaults to empty string if omitted from `rules.yaml` |

### ScheduleConfig

The `schedule:` block from `rules.yaml`.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `on` | `str` | required | Cron expression (`"0 16 * * 1-5"`) |
| `timezone` | `str` | `"America/New_York"` | Timezone for the cron trigger |

> **YAML note:** The key `on` must be quoted (`"on":`) in `rules.yaml` because bare `on` is parsed as the boolean `True` by PyYAML (YAML 1.1 behaviour). The existing `config/rules.yaml` already quotes it.

### RulesConfig

Top-level container returned by `load_rules_config`.

| Field | Type | Description |
|-------|------|-------------|
| `schedule` | `ScheduleConfig` | Cron schedule for APScheduler |
| `rules` | `list[RuleConfig]` | Ordered list of weighted rules |

---

## load_rules_config(path)

```python
from pathlib import Path
from screener.config import load_rules_config

cfg = load_rules_config(Path("config/rules.yaml"))
for rule in cfg.rules:
    print(rule.name, rule.weight, rule.condition)
# big_tech_or_chip 2.0 in_watchlist or is_chip
# oversold_band    0.6 rsi(14) > 25 and rsi(14) < 40
# quality_uptrend  1.5 close > sma(200)
# near_52w_low     1.0 close <= low_52w(252) * 1.15
# undervalued_pb   1.5 price_to_book < 4
```

Reads the YAML file at `path`, passes the parsed dict to `RulesConfig.model_validate()`, and returns the validated model. Raises `pydantic_core.ValidationError` if the YAML structure does not match the schema, and `FileNotFoundError` / `yaml.YAMLError` for I/O or parse problems.

---

## load_watchlist(path) -> set[str]

```python
from pathlib import Path
from screener.config import load_watchlist

watchlist = load_watchlist(Path("config/watchlist.yaml"))
print(watchlist)
# {"AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AMD",
#  "AVGO", "TSM", "INTC", "QCOM", "MU", "ASML", "ARM", "SMCI"}
```

Reads the YAML file at `path`, `yaml.safe_load`s it, and returns `{str(s) for s in data["symbols"]}` — a plain `set[str]` of ticker symbols (not a Pydantic model, unlike the other loaders on this page). Used throughout `main.py` and `backtest/engine.py` to build the curated big-tech/chip list consulted by the `"losers"` universe and the `big_tech_or_chip` rule.

Error behavior: no bespoke validation is performed, so failures surface as whatever the underlying calls raise — `FileNotFoundError` if `path` doesn't exist, `yaml.YAMLError` for malformed YAML, `TypeError` if the file parses to `None` (empty file) or another type without a `symbols` key to subscript, and `KeyError` if the top-level `symbols:` key is missing entirely.

---

## Quality Screen Config Model

Represents the parsed `config/quality_screen.yaml` file — the 6 fixed thresholds used by `evaluate_quality_gate()` (see `docs/rules.md`).

### QualityScreenConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `min_fcf_5y_cumulative` | `float` | `0.0` | Minimum cumulative 5-year free cash flow |
| `min_interest_coverage` | `float` | `2.0` | Minimum latest-year EBIT / interest expense |
| `min_gross_margin` | `float` | `0.15` | Minimum average gross margin |
| `min_ocf_ni_ratio` | `float` | `0.7` | Minimum average operating-cash-flow / net-income ratio |
| `min_net_margin` | `float` | `0.05` | Minimum average net margin |
| `max_share_dilution_5y` | `float` | `0.20` | Maximum share-count growth over the available history |

---

## load_quality_screen_config(path)

```python
from pathlib import Path
from screener.config import load_quality_screen_config

cfg = load_quality_screen_config(Path("config/quality_screen.yaml"))
print(cfg.min_interest_coverage)  # 2.0
```

Reads the YAML file at `path`, passes the parsed dict to `QualityScreenConfig.model_validate()`, and returns the validated model. Same error semantics as `load_rules_config`.

---

## File locations

| File | Purpose |
|------|---------|
| `backend/.env` | Runtime secrets and overrides (gitignored) |
| `backend/.env.example` | Template showing all supported env vars |
| `backend/config/rules.yaml` | Cron schedule + 5 weighted rules |
| `backend/config/quality_screen.yaml` | 6 fixed thresholds for the fundamentals quality gate |
| `backend/config/universe.yaml` | Fixed 10-symbol list used by the `"static"` universe |
| `backend/config/watchlist.yaml` | 16-symbol curated big-tech/chip list, parsed by `load_watchlist()` |
