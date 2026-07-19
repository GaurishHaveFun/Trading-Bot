"""Tests for the Postgres DB output writer. All tests here are offline —
no live Postgres is available in CI/local unit test runs — so the psycopg
connection boundary (`psycopg.AsyncConnection.connect`) is mocked with a
lightweight fake connection that records every `execute()` call, letting us
assert on the exact SQL/params shape without a real database."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from screener.models import BacktestResult, BacktestTrade, Bar, RuleAttribution, RuleResult, ScreenerRun, Signal
from screener.output import db_writer
from screener.output.db_writer import write_backtest_to_db, write_run_to_db
from psycopg.types.json import Jsonb


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class _FakeConnection:
    """Records every execute() call as (sql, params) and hands back an
    incrementing id for any statement (mimics RETURNING id)."""

    def __init__(self):
        self.calls: list[tuple[str, tuple | None]] = []
        self._next_id = 1

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        row_id = self._next_id
        self._next_id += 1
        return _FakeCursor((row_id,))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _patch_connect(monkeypatch, fake_conn: _FakeConnection) -> None:
    monkeypatch.setattr(
        db_writer.psycopg.AsyncConnection,
        "connect",
        AsyncMock(return_value=fake_conn),
    )


def _make_run() -> ScreenerRun:
    rule_results = [
        RuleResult(rule_name="oversold_band", passed=True, weight=0.6, detail={"rsi_14": 31.2}),
        RuleResult(rule_name="quality_uptrend", passed=False, weight=1.5, detail={}),
    ]
    signal = Signal(
        ticker="AAPL",
        timestamp=datetime(2024, 1, 15, 20, 0, 0, tzinfo=timezone.utc),
        score=0.6 / 2.1,
        rules_passed=1,
        rules_total=2,
        rule_results=rule_results,
        snapshot={"close": 192.31, "volume": 54000000, "in_watchlist": True},
    )
    return ScreenerRun(
        run_timestamp=datetime(2024, 1, 15, 20, 1, 14, tzinfo=timezone.utc),
        universe="static",
        alert_threshold=0.70,
        signals=[signal],
    )


def _make_bars() -> list[Bar]:
    return [
        Bar(
            timestamp=datetime(2024, 1, 15, tzinfo=timezone.utc),
            open=190.0, high=193.0, low=189.0, close=192.31, volume=54_000_000,
        )
    ]


def _make_backtest_result() -> BacktestResult:
    trade = BacktestTrade(
        ticker="AAPL",
        signal_date=datetime(2024, 1, 15, tzinfo=timezone.utc),
        score=0.75,
        rules_passed=3,
        rules_total=5,
        buy_close=190.0,
        sell_date=datetime(2024, 1, 22, tzinfo=timezone.utc),
        sell_close=200.0,
        return_pct=5.26,
        win=True,
    )
    attribution = RuleAttribution(
        rule_name="oversold_band",
        weight=0.6,
        passed_count=10,
        passed_win_rate=0.6,
        passed_avg_return_pct=1.5,
        failed_count=20,
        failed_win_rate=0.4,
        failed_avg_return_pct=-0.5,
        edge_pct=2.0,
    )
    return BacktestResult(
        universe="watchlist",
        holding_days=5,
        alert_threshold=0.70,
        lookback_days=30,
        start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 1, 31, tzinfo=timezone.utc),
        total_signals=1,
        wins=1,
        losses=0,
        win_rate=1.0,
        avg_return_pct=5.26,
        total_return_pct=5.26,
        best_trade_return_pct=5.26,
        worst_trade_return_pct=5.26,
        baseline_avg_return_pct=1.0,
        trades=[trade],
        rule_attribution=[attribution],
    )


# --- _ensure_schema ---

async def test_ensure_schema_creates_all_tables_and_indexes():
    fake_conn = _FakeConnection()
    await db_writer._ensure_schema(fake_conn)

    statements = [sql for sql, _params in fake_conn.calls]
    assert any("CREATE TABLE IF NOT EXISTS runs" in s for s in statements)
    assert any("CREATE TABLE IF NOT EXISTS signals" in s for s in statements)
    assert any("CREATE TABLE IF NOT EXISTS rule_results" in s for s in statements)
    assert any("CREATE TABLE IF NOT EXISTS bars" in s for s in statements)
    assert any("CREATE TABLE IF NOT EXISTS backtests" in s for s in statements)
    assert any("CREATE TABLE IF NOT EXISTS backtest_trades" in s for s in statements)
    assert any("CREATE TABLE IF NOT EXISTS rule_attributions" in s for s in statements)
    assert any("CREATE INDEX IF NOT EXISTS idx_signals_run_score" in s for s in statements)
    assert any("CREATE INDEX IF NOT EXISTS idx_signals_ticker" in s for s in statements)
    assert any("CREATE INDEX IF NOT EXISTS idx_backtest_trades_backtest" in s for s in statements)
    assert any("CREATE INDEX IF NOT EXISTS idx_rule_attributions_backtest" in s for s in statements)
    # none of the DDL statements should have been executed with bound params
    assert all(params is None for _sql, params in fake_conn.calls)


# --- write_run_to_db ---

async def test_write_run_to_db_is_async_and_awaitable(monkeypatch):
    fake_conn = _FakeConnection()
    _patch_connect(monkeypatch, fake_conn)

    result = write_run_to_db(_make_run(), bars_by_ticker={}, database_url="postgres://fake")
    assert hasattr(result, "__await__")
    await result


async def test_write_run_to_db_empty_url_raises():
    with pytest.raises(ValueError):
        await write_run_to_db(_make_run(), bars_by_ticker={}, database_url="")


async def test_write_run_to_db_inserts_run_row(monkeypatch):
    fake_conn = _FakeConnection()
    _patch_connect(monkeypatch, fake_conn)
    run = _make_run()

    await write_run_to_db(run, bars_by_ticker={}, database_url="postgres://fake")

    run_inserts = [(sql, params) for sql, params in fake_conn.calls if "INSERT INTO runs" in sql]
    assert len(run_inserts) == 1
    sql, params = run_inserts[0]
    assert "ON CONFLICT (run_timestamp)" in sql
    assert params == (run.run_timestamp, run.universe, run.alert_threshold, len(run.signals))


async def test_write_run_to_db_inserts_signal_with_jsonb_snapshot(monkeypatch):
    fake_conn = _FakeConnection()
    _patch_connect(monkeypatch, fake_conn)
    run = _make_run()
    signal = run.signals[0]

    await write_run_to_db(run, bars_by_ticker={}, database_url="postgres://fake")

    signal_inserts = [(sql, params) for sql, params in fake_conn.calls if "INSERT INTO signals" in sql]
    assert len(signal_inserts) == 1
    _sql, params = signal_inserts[0]
    # run_id, ticker, timestamp, score, rules_passed, rules_total, snapshot
    assert params[1] == "AAPL"
    assert params[2] == signal.timestamp
    assert params[3] == signal.score
    assert params[4] == signal.rules_passed
    assert params[5] == signal.rules_total
    snapshot_param = params[6]
    assert isinstance(snapshot_param, Jsonb)
    assert snapshot_param.obj == signal.snapshot


async def test_write_run_to_db_inserts_rule_results_with_jsonb_detail(monkeypatch):
    fake_conn = _FakeConnection()
    _patch_connect(monkeypatch, fake_conn)
    run = _make_run()

    await write_run_to_db(run, bars_by_ticker={}, database_url="postgres://fake")

    rule_result_inserts = [(sql, params) for sql, params in fake_conn.calls if "INSERT INTO rule_results" in sql]
    assert len(rule_result_inserts) == 2
    _sql, params = rule_result_inserts[0]
    # signal_id, rule_name, passed, weight, detail
    assert params[1] == "oversold_band"
    assert params[2] is True
    assert params[3] == 0.6
    detail_param = params[4]
    assert isinstance(detail_param, Jsonb)
    assert detail_param.obj == {"rsi_14": 31.2}


async def test_write_run_to_db_inserts_bars_for_signal_tickers_only(monkeypatch):
    fake_conn = _FakeConnection()
    _patch_connect(monkeypatch, fake_conn)
    run = _make_run()
    bars = _make_bars()

    await write_run_to_db(
        run, bars_by_ticker={"AAPL": bars, "IGNORED": bars}, database_url="postgres://fake"
    )

    bar_inserts = [(sql, params) for sql, params in fake_conn.calls if "INSERT INTO bars" in sql]
    # only the AAPL bar (the one signal ticker) should have been written,
    # even though bars_by_ticker also contained an "IGNORED" ticker
    assert len(bar_inserts) == 1
    sql, params = bar_inserts[0]
    assert "ON CONFLICT (ticker, timestamp) DO NOTHING" in sql
    assert params == (
        "AAPL", bars[0].timestamp, bars[0].open, bars[0].high, bars[0].low, bars[0].close, bars[0].volume,
    )


# --- write_backtest_to_db ---

async def test_write_backtest_to_db_is_async_and_awaitable(monkeypatch):
    fake_conn = _FakeConnection()
    _patch_connect(monkeypatch, fake_conn)

    result = write_backtest_to_db(_make_backtest_result(), database_url="postgres://fake")
    assert hasattr(result, "__await__")
    await result


async def test_write_backtest_to_db_empty_url_raises():
    with pytest.raises(ValueError):
        await write_backtest_to_db(_make_backtest_result(), database_url="")


async def test_write_backtest_to_db_inserts_backtest_row(monkeypatch):
    fake_conn = _FakeConnection()
    _patch_connect(monkeypatch, fake_conn)
    result = _make_backtest_result()

    await write_backtest_to_db(result, database_url="postgres://fake")

    backtest_inserts = [(sql, params) for sql, params in fake_conn.calls if "INSERT INTO backtests" in sql]
    assert len(backtest_inserts) == 1
    _sql, params = backtest_inserts[0]
    assert params == (
        result.universe, result.holding_days, result.alert_threshold, result.lookback_days,
        result.start_date, result.end_date, result.total_signals, result.wins, result.losses,
        result.win_rate, result.avg_return_pct, result.total_return_pct,
        result.best_trade_return_pct, result.worst_trade_return_pct, result.baseline_avg_return_pct,
    )


async def test_write_backtest_to_db_inserts_trades_and_rule_attribution(monkeypatch):
    fake_conn = _FakeConnection()
    _patch_connect(monkeypatch, fake_conn)
    result = _make_backtest_result()
    trade = result.trades[0]
    attribution = result.rule_attribution[0]

    await write_backtest_to_db(result, database_url="postgres://fake")

    trade_inserts = [(sql, params) for sql, params in fake_conn.calls if "INSERT INTO backtest_trades" in sql]
    assert len(trade_inserts) == 1
    _sql, params = trade_inserts[0]
    assert params[1:] == (
        trade.ticker, trade.signal_date, trade.score, trade.rules_passed, trade.rules_total,
        trade.buy_close, trade.sell_date, trade.sell_close, trade.return_pct, trade.win,
    )

    attribution_inserts = [
        (sql, params) for sql, params in fake_conn.calls if "INSERT INTO rule_attributions" in sql
    ]
    assert len(attribution_inserts) == 1
    _sql, params = attribution_inserts[0]
    assert params[1:] == (
        attribution.rule_name, attribution.weight, attribution.passed_count,
        attribution.passed_win_rate, attribution.passed_avg_return_pct,
        attribution.failed_count, attribution.failed_win_rate,
        attribution.failed_avg_return_pct, attribution.edge_pct,
    )
