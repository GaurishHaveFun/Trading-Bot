# CLAUDE.md — Trading Bot (Phase 1: Stock Screener)

## Project Overview

Phase 1 of a multi-phase trading bot. A Python batch/scheduled service that: pulls daily OHLCV bars for a universe of stocks → computes technical indicators → evaluates weighted rules via a safe expression engine → emits a locked JSON output file. Runs via `--once` (single run), `--ticker AAPL` (debug single ticker), or bare (cron scheduler).

## Layout

Everything is under `backend/`:

```
trading-bot/
├── CLAUDE.md
├── README.md
└── backend/
    ├── pyproject.toml        # uv project, requires-python = ">=3.12,<3.13"
    ├── .python-version       # 3.12
    ├── .env.example
    ├── config/
    │   ├── rules.yaml        # cron schedule + 5 weighted rules
    │   └── universe.yaml     # 10 ticker symbols
    ├── docs/                 # one .md per module (written by Sonnet subagents after each module is built)
    ├── src/screener/
    │   ├── main.py           # orchestration + CLI only
    │   ├── config.py         # pydantic-settings
    │   ├── models.py         # all Pydantic data models
    │   ├── universe/         # UniverseProvider base + StaticUniverse + SP500Universe
    │   ├── data/             # DataProvider base + BarCache (SQLite) + YFinanceProvider
    │   ├── indicators/       # library.py: sma, ema, rsi, atr, sma_volume
    │   ├── rules/            # engine.py + functions.py (asteval-powered)
    │   ├── output/           # json_writer.py
    │   └── utils/            # logging.py (structlog JSON)
    └── tests/
        ├── fixtures/         # aapl_sample.csv
        └── test_*.py
```

No frontend this phase — web dashboard is out-of-scope for Phase 1 and will be added as a sibling `frontend/` directory in a later phase.

## Tech Stack

- Python 3.12 (pinned via uv — pandas-ta requires ≥3.12)
- uv for project/dependency management
- pydantic + pydantic-settings (models + config)
- pandas + numpy (data manipulation)
- yfinance (primary data source — MVP only)
- httpx (async HTTP for future providers)
- sqlite3 stdlib (bar cache)
- pandas-ta (technical indicators)
- asteval (safe rule expression evaluation — NEVER use `eval`)
- APScheduler (cron scheduling)
- structlog (JSON logging)
- pytest (tests)

Future API keys (stubbed in .env.example): `FINNHUB_API_KEY`, `ALPACA_KEY`, `ALPACA_SECRET` — the data provider layer is designed to swap in these APIs in later phases.

## Non-Negotiables (spec-enforced)

1. **All times UTC internally.** Convert only at I/O edges. Use `datetime.timezone.utc` or pandas UTC-aware timestamps.
2. **Never use `eval()`.** Rule expressions must go through `asteval.Interpreter` only.
3. **All network I/O is async.** Use `asyncio.to_thread` for blocking libraries (yfinance). Cap concurrency with `asyncio.Semaphore(10)`.
4. **Cache every yfinance fetch to SQLite.** `BarCache.get/put` in `data/cache.py`.
5. **Skip tickers with <200 bars.** Never return NaN in a `Signal`.
6. **Structured JSON logs only.** No `print()` in library code. Use `structlog` logger throughout.
7. **Every module gets at least one unit test.** Integration tests (real network) are marked `@pytest.mark.integration` and skippable.
8. **No business logic in `main.py`.** Only orchestration/CLI wiring.

## Locked Output Format (Phase 3 consumes this — do not change the schema)

```json
{
  "run_timestamp": "2026-06-27T20:01:14Z",
  "universe": "static",
  "alert_threshold": 0.70,
  "signals": [
    {
      "ticker": "AAPL",
      "timestamp": "2026-06-27T20:00:00Z",
      "score": 0.85,
      "rules_passed": 4,
      "rules_total": 5,
      "snapshot": {"close": 192.31, "volume": 54000000, "rsi_14": 31.2, "sma_50": 188.4, "sma_200": 175.1, "atr_14": 4.2},
      "rule_results": [
        {"rule_name": "oversold_rsi", "passed": true, "weight": 2.0, "detail": {"rsi_14": 31.2, "threshold": 35}}
      ]
    }
  ]
}
```

## Rules (from config/rules.yaml)

| Name | Weight | Condition |
|------|--------|-----------|
| oversold_rsi | 2.0 | `rsi(14) < 35` |
| above_long_trend | 1.5 | `close > sma(200)` |
| golden_cross_state | 1.5 | `sma(50) > sma(200)` |
| volume_spike | 1.0 | `volume > sma_volume(20) * 1.5` |
| reasonable_volatility | 1.0 | `atr(14) / close < 0.05` |

## Development Workflow

- Each build step: implement → `uv run pytest` must pass → show diff → wait for user approval before next step.
- Docs: after each module is implemented and tested, a Sonnet subagent writes `backend/docs/<module>.md`.
- pandas-ta fallback: if pandas-ta fails to install or run, STOP and ask the user — do not silently swap to another library.

## Running the Project

```bash
cd backend
uv run python -m screener.main --once        # single run, writes output/runs/run_<UTC>.json
uv run python -m screener.main --ticker AAPL # debug: verbose per-rule breakdown
uv run python -m screener.main               # start cron scheduler
uv run pytest                                # run all unit tests
uv run pytest -m "not integration"           # skip network tests
```

## Definition of Done (Phase 1)

- `uv run pytest` passes (all non-integration tests).
- `uv run python -m screener.main --once` produces valid JSON in `output/runs/` in under 60 seconds.
- `--ticker AAPL` prints a readable per-rule breakdown to the console.
- No NaN values in any Signal, all timestamps UTC, JSON logs only.
