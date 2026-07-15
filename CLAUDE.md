# CLAUDE.md — Trading Bot (Phase 1: Stock Screener)

## Project Overview

Phase 1 of a multi-phase trading bot. A Python batch/scheduled service that: pulls daily OHLCV bars for a universe of stocks → computes technical indicators → filters out tickers that fail a fundamentals-based quality gate → evaluates weighted rules via a safe expression engine → emits a locked JSON output file plus a human-readable PDF report. Runs via `--once` (single run), `--ticker AAPL` (debug single ticker), `--backtest` (historical backtest of the current rules), or bare (cron scheduler).

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
    │   ├── rules.yaml            # cron schedule + 5 weighted rules
    │   ├── universe.yaml         # 10 ticker symbols (static fallback universe)
    │   ├── watchlist.yaml        # 16 ticker big-tech/chip watchlist
    │   └── quality_screen.yaml   # fundamentals quality-gate thresholds
    ├── docs/                 # one .md per module (written by Sonnet subagents after each module is built)
    ├── src/screener/
    │   ├── main.py           # orchestration + CLI only
    │   ├── config.py         # pydantic-settings
    │   ├── models.py         # all Pydantic data models
    │   ├── scheduler.py      # APScheduler cron loop
    │   ├── universe/         # UniverseProvider base + StaticUniverse + LosersUniverse + SP500Universe
    │   ├── data/             # DataProvider base + BarCache/FundamentalsCache (SQLite) + YFinanceProvider + FundamentalsProvider
    │   ├── indicators/       # library.py: sma, ema, rsi, atr, sma_volume, low_52w, high_52w, macd_line, macd_signal_line, macd_histogram
    │   ├── rules/            # engine.py + functions.py + quality_gate.py (asteval-powered)
    │   ├── output/           # json_writer.py + pdf_writer.py
    │   ├── backtest/         # engine.py: historical backtest of current rules over the watchlist
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
- reportlab (PDF report generation)
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
      "snapshot": {"close": 192.31, "volume": 54000000, "rsi_14": 31.2, "sma_50": 188.4, "sma_200": 175.1, "atr_14": 4.2, "price_to_book": 3.1, "change_pct": -1.85, "in_watchlist": true, "industry": "Consumer Electronics", "is_chip": false},
      "rule_results": [
        {"rule_name": "oversold_band", "passed": true, "weight": 0.6, "detail": {"close": 192.31, "volume": 54000000, "in_watchlist": true, "is_chip": false, "price_to_book": 3.1, "rsi_14": 31.2}}
      ]
    }
  ]
}
```

## Rules (from config/rules.yaml)

| Name | Weight | Condition |
|------|--------|-----------|
| big_tech_or_chip | 2.0 | `in_watchlist or is_chip` |
| oversold_band | 0.6 | `rsi(14) > 25 and rsi(14) < 40` |
| quality_uptrend | 1.5 | `close > sma(200)` |
| medium_term_momentum | 1.0 | `sma(50) > sma(100)` |
| macd_bullish | 1.0 | `macd_line() > macd_signal_line()` |
| near_52w_low | 0.5 | `close <= low_52w(252) * 1.15` |
| undervalued_pb | 1.5 | `price_to_book < 4` |

## Development Workflow

- Each build step: implement → `uv run pytest` must pass → show diff → wait for user approval before next step.
- Docs: after each module is implemented and tested, a Sonnet subagent writes `backend/docs/<module>.md`.
- pandas-ta fallback: if pandas-ta fails to install or run, STOP and ask the user — do not silently swap to another library.

## Running the Project

```bash
cd backend
uv run python -m screener.main --once        # single run, writes output/runs/run_<UTC>.json + output/reports/report_<UTC>.pdf
uv run python -m screener.main --ticker AAPL # debug: verbose per-rule breakdown + output/reports/report_AAPL_<UTC>.pdf
uv run python -m screener.main --backtest    # historical backtest (--days, default 30; --hold, default 5), writes output/reports/backtest_<UTC>.pdf
uv run python -m screener.main               # start cron scheduler
uv run pytest                                # run all unit tests
uv run pytest -m "not integration"           # skip network tests
```

## Definition of Done (Phase 1)

- `uv run pytest` passes (all non-integration tests).
- `uv run python -m screener.main --once` produces valid JSON in `output/runs/` and a PDF report in `output/reports/` in under 60 seconds.
- `--ticker AAPL` prints a readable per-rule breakdown to the console and writes a per-ticker PDF report.
- Tickers that fail the fundamentals-based quality gate are excluded from scoring before rules run.
- No NaN values in any Signal, all timestamps UTC, JSON logs only.
