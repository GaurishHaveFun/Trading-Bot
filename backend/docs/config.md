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

The API key fields are stubs for Phase 2+ providers (Finnhub, Alpaca). They are intentionally empty strings in Phase 1 because only yfinance is used.

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
# oversold_rsi   2.0  rsi(14) < 35
# above_long_trend 1.5 close > sma(200)
# ...
```

Reads the YAML file at `path`, passes the parsed dict to `RulesConfig.model_validate()`, and returns the validated model. Raises `pydantic_core.ValidationError` if the YAML structure does not match the schema, and `FileNotFoundError` / `yaml.YAMLError` for I/O or parse problems.

---

## File locations

| File | Purpose |
|------|---------|
| `backend/.env` | Runtime secrets and overrides (gitignored) |
| `backend/.env.example` | Template showing all supported env vars |
| `backend/config/rules.yaml` | Cron schedule + 5 weighted rules |
