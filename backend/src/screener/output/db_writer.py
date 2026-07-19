"""Writes ScreenerRun / BacktestResult data to a hosted Postgres DB (Neon).

This is a THIRD, purely additive output alongside the locked-schema JSON
(`json_writer.py`) and the human-readable PDF (`pdf_writer.py`). It never
changes what those two write or return. Callers in `main.py` guard on
`settings.database_url` truthiness before calling anything here, and wrap
the call in a try/except that logs and continues — matching the graceful
degradation pattern used throughout `screener.data` for optional/fallback-
capable integrations (e.g. the Schwab -> yfinance fallback in
`data/factory.py`), except here there is no fallback source: a DB write
failure just means this run's data doesn't show up in the (future) web
dashboard, nothing else is affected.

Schema is created idempotently on every write (`CREATE TABLE IF NOT EXISTS`,
`CREATE INDEX IF NOT EXISTS`) — same idiom as the local SQLite bar cache in
`data/cache.py`, adapted to Postgres/psycopg. No separate migration tooling.
"""
from __future__ import annotations

import psycopg
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from screener.models import BacktestResult, BacktestTrade, Bar, RuleAttribution, RuleResult, ScreenerRun, Signal
from screener.utils.logging import get_logger

logger = get_logger(__name__)

_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS runs (
        id BIGSERIAL PRIMARY KEY,
        run_timestamp TIMESTAMPTZ NOT NULL UNIQUE,
        universe TEXT NOT NULL,
        alert_threshold DOUBLE PRECISION NOT NULL,
        signal_count INTEGER NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signals (
        id BIGSERIAL PRIMARY KEY,
        run_id BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        ticker TEXT NOT NULL,
        timestamp TIMESTAMPTZ NOT NULL,
        score DOUBLE PRECISION NOT NULL,
        rules_passed INTEGER NOT NULL,
        rules_total INTEGER NOT NULL,
        snapshot JSONB NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_signals_run_score ON signals (run_id, score DESC)",
    "CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals (ticker)",
    """
    CREATE TABLE IF NOT EXISTS rule_results (
        id BIGSERIAL PRIMARY KEY,
        signal_id BIGINT NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
        rule_name TEXT NOT NULL,
        passed BOOLEAN NOT NULL,
        weight DOUBLE PRECISION NOT NULL,
        detail JSONB NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bars (
        ticker TEXT NOT NULL,
        timestamp TIMESTAMPTZ NOT NULL,
        open DOUBLE PRECISION NOT NULL,
        high DOUBLE PRECISION NOT NULL,
        low DOUBLE PRECISION NOT NULL,
        close DOUBLE PRECISION NOT NULL,
        volume BIGINT NOT NULL,
        PRIMARY KEY (ticker, timestamp)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS backtests (
        id BIGSERIAL PRIMARY KEY,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        universe TEXT, holding_days INTEGER, alert_threshold DOUBLE PRECISION,
        lookback_days INTEGER, start_date TIMESTAMPTZ, end_date TIMESTAMPTZ,
        total_signals INTEGER, wins INTEGER, losses INTEGER, win_rate DOUBLE PRECISION,
        avg_return_pct DOUBLE PRECISION, total_return_pct DOUBLE PRECISION,
        best_trade_return_pct DOUBLE PRECISION, worst_trade_return_pct DOUBLE PRECISION,
        baseline_avg_return_pct DOUBLE PRECISION
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS backtest_trades (
        id BIGSERIAL PRIMARY KEY,
        backtest_id BIGINT NOT NULL REFERENCES backtests(id) ON DELETE CASCADE,
        ticker TEXT, signal_date TIMESTAMPTZ, score DOUBLE PRECISION,
        rules_passed INTEGER, rules_total INTEGER, buy_close DOUBLE PRECISION,
        sell_date TIMESTAMPTZ, sell_close DOUBLE PRECISION, return_pct DOUBLE PRECISION, win BOOLEAN
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_backtest_trades_backtest ON backtest_trades (backtest_id)",
    """
    CREATE TABLE IF NOT EXISTS rule_attributions (
        id BIGSERIAL PRIMARY KEY,
        backtest_id BIGINT NOT NULL REFERENCES backtests(id) ON DELETE CASCADE,
        rule_name TEXT, weight DOUBLE PRECISION, passed_count INTEGER,
        passed_win_rate DOUBLE PRECISION, passed_avg_return_pct DOUBLE PRECISION,
        failed_count INTEGER, failed_win_rate DOUBLE PRECISION,
        failed_avg_return_pct DOUBLE PRECISION, edge_pct DOUBLE PRECISION
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_rule_attributions_backtest ON rule_attributions (backtest_id)",
)


async def _ensure_schema(conn: AsyncConnection) -> None:
    """Idempotently create all 7 tables + indexes. Safe to run on every write."""
    for statement in _SCHEMA_STATEMENTS:
        await conn.execute(statement)


async def write_run_to_db(
    run: ScreenerRun,
    bars_by_ticker: dict[str, list[Bar]],
    database_url: str,
) -> None:
    """Write a ScreenerRun — its signals, rule results, and per-signal bars —
    to Postgres in a single transaction.

    `bars_by_ticker` should contain bars only for tickers that produced a
    signal (not the whole universe) — the caller in main.py is responsible
    for that filtering. `database_url` must be non-empty; the "unconfigured,
    skip DB writes" decision belongs at the call site (`main.py` guards on
    `settings.database_url` truthiness before calling this at all). Passing
    an empty string here raises `ValueError` rather than silently no-op'ing,
    so a caller that forgets the guard fails loudly instead of losing data
    silently.
    """
    if not database_url:
        raise ValueError("write_run_to_db requires a non-empty database_url")

    async with await psycopg.AsyncConnection.connect(database_url) as conn:
        await _ensure_schema(conn)

        run_id = await _insert_run(conn, run)
        for signal in run.signals:
            signal_id = await _insert_signal(conn, run_id, signal)
            for rule_result in signal.rule_results:
                await _insert_rule_result(conn, signal_id, rule_result)
            for bar in bars_by_ticker.get(signal.ticker, []):
                await _insert_bar(conn, signal.ticker, bar)

    logger.info("run_written_to_db", run_id=run_id, signals=len(run.signals))


async def write_backtest_to_db(result: BacktestResult, database_url: str) -> None:
    """Write a BacktestResult — its trades and per-rule attribution — to
    Postgres in a single transaction. See `write_run_to_db` for the
    empty-`database_url` contract."""
    if not database_url:
        raise ValueError("write_backtest_to_db requires a non-empty database_url")

    async with await psycopg.AsyncConnection.connect(database_url) as conn:
        await _ensure_schema(conn)

        backtest_id = await _insert_backtest(conn, result)
        for trade in result.trades:
            await _insert_backtest_trade(conn, backtest_id, trade)
        for attribution in result.rule_attribution:
            await _insert_rule_attribution(conn, backtest_id, attribution)

    logger.info(
        "backtest_written_to_db",
        backtest_id=backtest_id,
        trades=len(result.trades),
        rules=len(result.rule_attribution),
    )


async def _insert_run(conn: AsyncConnection, run: ScreenerRun) -> int:
    # ON CONFLICT ... DO UPDATE (a no-op update of the same value) rather
    # than DO NOTHING so RETURNING always yields the row's id, whether this
    # is the first write or a retry of the same run_timestamp.
    cur = await conn.execute(
        """
        INSERT INTO runs (run_timestamp, universe, alert_threshold, signal_count)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (run_timestamp) DO UPDATE SET run_timestamp = EXCLUDED.run_timestamp
        RETURNING id
        """,
        (run.run_timestamp, run.universe, run.alert_threshold, len(run.signals)),
    )
    row = await cur.fetchone()
    return row[0]


async def _insert_signal(conn: AsyncConnection, run_id: int, signal: Signal) -> int:
    cur = await conn.execute(
        """
        INSERT INTO signals (run_id, ticker, timestamp, score, rules_passed, rules_total, snapshot)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            run_id,
            signal.ticker,
            signal.timestamp,
            signal.score,
            signal.rules_passed,
            signal.rules_total,
            Jsonb(signal.snapshot),
        ),
    )
    row = await cur.fetchone()
    return row[0]


async def _insert_rule_result(conn: AsyncConnection, signal_id: int, rule_result: RuleResult) -> None:
    await conn.execute(
        """
        INSERT INTO rule_results (signal_id, rule_name, passed, weight, detail)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (signal_id, rule_result.rule_name, rule_result.passed, rule_result.weight, Jsonb(rule_result.detail)),
    )


async def _insert_bar(conn: AsyncConnection, ticker: str, bar: Bar) -> None:
    # ON CONFLICT DO NOTHING: the same (ticker, timestamp) bar legitimately
    # gets re-written every run the ticker is a signal in.
    await conn.execute(
        """
        INSERT INTO bars (ticker, timestamp, open, high, low, close, volume)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (ticker, timestamp) DO NOTHING
        """,
        (ticker, bar.timestamp, bar.open, bar.high, bar.low, bar.close, bar.volume),
    )


async def _insert_backtest(conn: AsyncConnection, result: BacktestResult) -> int:
    cur = await conn.execute(
        """
        INSERT INTO backtests (
            universe, holding_days, alert_threshold, lookback_days, start_date, end_date,
            total_signals, wins, losses, win_rate, avg_return_pct, total_return_pct,
            best_trade_return_pct, worst_trade_return_pct, baseline_avg_return_pct
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            result.universe,
            result.holding_days,
            result.alert_threshold,
            result.lookback_days,
            result.start_date,
            result.end_date,
            result.total_signals,
            result.wins,
            result.losses,
            result.win_rate,
            result.avg_return_pct,
            result.total_return_pct,
            result.best_trade_return_pct,
            result.worst_trade_return_pct,
            result.baseline_avg_return_pct,
        ),
    )
    row = await cur.fetchone()
    return row[0]


async def _insert_backtest_trade(conn: AsyncConnection, backtest_id: int, trade: BacktestTrade) -> None:
    await conn.execute(
        """
        INSERT INTO backtest_trades (
            backtest_id, ticker, signal_date, score, rules_passed, rules_total,
            buy_close, sell_date, sell_close, return_pct, win
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            backtest_id,
            trade.ticker,
            trade.signal_date,
            trade.score,
            trade.rules_passed,
            trade.rules_total,
            trade.buy_close,
            trade.sell_date,
            trade.sell_close,
            trade.return_pct,
            trade.win,
        ),
    )


async def _insert_rule_attribution(conn: AsyncConnection, backtest_id: int, attribution: RuleAttribution) -> None:
    await conn.execute(
        """
        INSERT INTO rule_attributions (
            backtest_id, rule_name, weight, passed_count, passed_win_rate, passed_avg_return_pct,
            failed_count, failed_win_rate, failed_avg_return_pct, edge_pct
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            backtest_id,
            attribution.rule_name,
            attribution.weight,
            attribution.passed_count,
            attribution.passed_win_rate,
            attribution.passed_avg_return_pct,
            attribution.failed_count,
            attribution.failed_win_rate,
            attribution.failed_avg_return_pct,
            attribution.edge_pct,
        ),
    )
