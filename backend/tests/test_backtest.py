"""Tests for the backtest package. All tests here are offline — no network —
so they run under `pytest -m "not integration"`."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from screener.backtest import evaluate_symbol
from screener.backtest.engine import _aggregate_rule_attribution
from screener.config import RuleConfig
from screener.models import Bar, BacktestResult, BacktestTrade, RuleAttribution
from screener.output.pdf_writer import write_backtest_report
from screener.rules import RuleEngine

_MIN_BARS = 200
_BASE = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _make_bars(n: int, overrides: dict[int, float] | None = None, base_close: float = 100.0) -> list[Bar]:
    """Ascending bars, one per day, all closing at `base_close` unless
    `overrides` sets a specific index's close."""
    overrides = overrides or {}
    bars = []
    for i in range(n):
        close = overrides.get(i, base_close)
        bars.append(
            Bar(
                timestamp=_BASE + timedelta(days=i),
                open=close - 1,
                high=close + 2,
                low=close - 2,
                close=close,
                volume=1_000_000,
            )
        )
    return bars


def _engine(condition: str = "close > 150", weight: float = 1.0) -> RuleEngine:
    return RuleEngine([RuleConfig(name="test_rule", weight=weight, condition=condition)])


# --- signal fires on a known day, exact return computed by hand ---

def test_evaluate_symbol_produces_one_trade_with_exact_return():
    n = 230
    holding_days = 5
    signal_index = 210  # well within the valid window (>=199, <=224)

    # Every bar closes at 100 except the signal day, which spikes to 200 so
    # "close > 150" passes exactly once. The forward bar (signal_index + 5)
    # is left at the unmodified base close of 100.
    bars = _make_bars(n, overrides={signal_index: 200.0})

    engine = _engine("close > 150", weight=1.0)
    trades, forward_returns, rule_evaluations = evaluate_symbol(
        symbol="TEST",
        bars=bars,
        engine=engine,
        watchlist=set(),
        threshold=1.0,
        holding_days=holding_days,
        eval_days=1000,  # large enough to cover every valid day
    )

    assert len(trades) == 1
    trade = trades[0]
    assert trade.ticker == "TEST"
    assert trade.buy_close == 200.0
    assert trade.sell_close == 100.0
    expected_return = (100.0 - 200.0) / 200.0 * 100  # -50.0
    assert trade.return_pct == pytest.approx(expected_return)
    assert trade.win is False
    assert trade.signal_date == bars[signal_index].timestamp
    assert trade.sell_date == bars[signal_index + holding_days].timestamp

    # Forward returns should still be populated for every valid evaluation
    # day, not just the one that fired a signal (used for the baseline calc).
    valid_days = (n - holding_days) - _MIN_BARS + 1
    assert len(forward_returns) == valid_days

    # Per-rule attribution: the rule ("close > 150") passes only on the
    # signal day itself (index 210), whose forward_return is -50.0. Every
    # other valid day's OWN close is the flat base 100.0, so the rule fails
    # on all of them — EXCEPT the day whose forward window's SELL bar lands
    # on the spike: day (signal_index - holding_days) = 205 buys at the flat
    # 100.0 close (rule still fails there) but sells at bars[210].close =
    # 200.0, giving forward_return = (200-100)/100*100 = +100.0. Every other
    # failed day has both endpoints at the flat 100.0 close, so its forward
    # return is exactly 0.0.
    assert "test_rule" in rule_evaluations
    assert len(rule_evaluations["test_rule"]) == valid_days

    passed_entries = [(p, r) for p, r in rule_evaluations["test_rule"] if p]
    assert len(passed_entries) == 1
    assert passed_entries[0][1] == pytest.approx(expected_return)

    failed_entries = [(p, r) for p, r in rule_evaluations["test_rule"] if not p]
    assert len(failed_entries) == valid_days - 1
    failed_returns = [r for _, r in failed_entries]
    nonzero_failed = [r for r in failed_returns if r != pytest.approx(0.0)]
    assert len(nonzero_failed) == 1
    assert nonzero_failed[0] == pytest.approx(100.0)  # day 205's sell bar hits the spike
    zero_failed = [r for r in failed_returns if r == pytest.approx(0.0)]
    assert len(zero_failed) == valid_days - 2


# --- signal never fires, but the baseline series is still populated ---

def test_evaluate_symbol_no_signal_still_populates_baseline():
    n = 230
    holding_days = 5
    bars = _make_bars(n, base_close=100.0)  # never exceeds threshold

    engine = _engine("close > 500", weight=1.0)  # impossible to satisfy
    trades, forward_returns, rule_evaluations = evaluate_symbol(
        symbol="TEST",
        bars=bars,
        engine=engine,
        watchlist=set(),
        threshold=1.0,
        holding_days=holding_days,
        eval_days=1000,
    )

    assert trades == []
    assert len(forward_returns) > 0
    # Flat closes -> every forward return is exactly 0.0
    assert all(r == pytest.approx(0.0) for r in forward_returns)


# --- last `holding_days` bars are excluded (no forward window) ---

def test_last_holding_days_bars_are_excluded():
    n = 230
    holding_days = 5
    # Spike a close inside the final holding_days bars (index 226, i.e. one
    # of the last 5) to try to trigger a signal — it must be structurally
    # excluded since there's no forward window to compute a sell price.
    excluded_index = n - 4  # 226, within the last 5 bars (225..229)
    bars = _make_bars(n, overrides={excluded_index: 500.0})

    engine = _engine("close > 150", weight=1.0)
    trades, forward_returns, rule_evaluations = evaluate_symbol(
        symbol="TEST",
        bars=bars,
        engine=engine,
        watchlist=set(),
        threshold=1.0,
        holding_days=holding_days,
        eval_days=1000,
    )

    assert trades == []  # the spike is never evaluated
    last_valid_index = n - 1 - holding_days
    assert last_valid_index < excluded_index
    valid_days = (n - holding_days) - _MIN_BARS + 1
    assert len(forward_returns) == valid_days


# --- eval_days windowing: only the trailing N valid days are walked ---

def test_eval_days_limits_the_window():
    n = 230
    holding_days = 5
    bars = _make_bars(n, base_close=100.0)
    engine = _engine("close > 500", weight=1.0)

    _, forward_returns, _ = evaluate_symbol(
        symbol="TEST",
        bars=bars,
        engine=engine,
        watchlist=set(),
        threshold=1.0,
        holding_days=holding_days,
        eval_days=10,
    )
    assert len(forward_returns) == 10


# --- _aggregate_rule_attribution ---

def test_aggregate_rule_attribution_mixed_passed_failed():
    rule_configs = [RuleConfig(name="rule_a", weight=2.0, condition="close > 0")]
    rule_evaluations = {
        "rule_a": [
            (True, 5.0), (True, -1.0), (True, 3.0),   # passed: avg=2.333.., win_rate=2/3
            (False, -2.0), (False, 4.0),               # failed: avg=1.0, win_rate=1/2
        ]
    }
    result = _aggregate_rule_attribution(rule_configs, rule_evaluations)
    assert len(result) == 1
    a = result[0]
    assert a.rule_name == "rule_a"
    assert a.weight == 2.0
    assert a.passed_count == 3
    assert a.passed_win_rate == pytest.approx(2 / 3)
    assert a.passed_avg_return_pct == pytest.approx((5.0 - 1.0 + 3.0) / 3)
    assert a.failed_count == 2
    assert a.failed_win_rate == pytest.approx(1 / 2)
    assert a.failed_avg_return_pct == pytest.approx((-2.0 + 4.0) / 2)
    assert a.edge_pct == pytest.approx(a.passed_avg_return_pct - a.failed_avg_return_pct)


def test_aggregate_rule_attribution_all_passed_no_failed_data():
    rule_configs = [RuleConfig(name="rule_a", weight=1.0, condition="close > 0")]
    rule_evaluations = {"rule_a": [(True, 5.0), (True, 3.0)]}
    result = _aggregate_rule_attribution(rule_configs, rule_evaluations)
    a = result[0]
    assert a.passed_count == 2
    assert a.passed_avg_return_pct == pytest.approx(4.0)
    assert a.failed_count == 0
    assert a.failed_win_rate == 0.0
    assert a.failed_avg_return_pct == 0.0
    assert a.edge_pct == 0.0


def test_aggregate_rule_attribution_all_failed_no_passed_data():
    rule_configs = [RuleConfig(name="rule_a", weight=1.0, condition="close > 0")]
    rule_evaluations = {"rule_a": [(False, -5.0), (False, -3.0)]}
    result = _aggregate_rule_attribution(rule_configs, rule_evaluations)
    a = result[0]
    assert a.failed_count == 2
    assert a.failed_avg_return_pct == pytest.approx(-4.0)
    assert a.passed_count == 0
    assert a.passed_win_rate == 0.0
    assert a.passed_avg_return_pct == 0.0
    assert a.edge_pct == 0.0


def test_aggregate_rule_attribution_no_data_for_rule():
    rule_configs = [RuleConfig(name="rule_a", weight=1.5, condition="close > 0")]
    result = _aggregate_rule_attribution(rule_configs, {})
    assert len(result) == 1
    a = result[0]
    assert a.rule_name == "rule_a"
    assert a.weight == 1.5
    assert a.passed_count == 0
    assert a.passed_win_rate == 0.0
    assert a.passed_avg_return_pct == 0.0
    assert a.failed_count == 0
    assert a.failed_win_rate == 0.0
    assert a.failed_avg_return_pct == 0.0
    assert a.edge_pct == 0.0


def test_aggregate_rule_attribution_preserves_rule_config_order():
    rule_configs = [
        RuleConfig(name="rule_c", weight=1.0, condition="close > 0"),
        RuleConfig(name="rule_a", weight=1.0, condition="close > 0"),
        RuleConfig(name="rule_b", weight=1.0, condition="close > 0"),
    ]
    rule_evaluations = {
        "rule_a": [(True, 1.0)],
        "rule_b": [],
        # rule_c intentionally absent from the dict
    }
    result = _aggregate_rule_attribution(rule_configs, rule_evaluations)
    assert [a.rule_name for a in result] == ["rule_c", "rule_a", "rule_b"]


# --- write_backtest_report: hand-built result, PDF magic bytes ---

def _make_result(
    trades: list[BacktestTrade], rule_attribution: list | None = None
) -> BacktestResult:
    wins = sum(1 for t in trades if t.win)
    losses = len(trades) - wins
    returns = [t.return_pct for t in trades]
    return BacktestResult(
        universe="watchlist",
        holding_days=5,
        alert_threshold=0.70,
        lookback_days=30,
        start_date=datetime(2026, 6, 1, tzinfo=timezone.utc),
        end_date=datetime(2026, 7, 1, tzinfo=timezone.utc),
        total_signals=len(trades),
        wins=wins,
        losses=losses,
        win_rate=(wins / len(trades)) if trades else 0.0,
        avg_return_pct=(sum(returns) / len(returns)) if returns else 0.0,
        total_return_pct=sum(returns),
        best_trade_return_pct=max(returns, default=0.0),
        worst_trade_return_pct=min(returns, default=0.0),
        baseline_avg_return_pct=0.85,
        trades=trades,
        rule_attribution=rule_attribution or [],
    )


def _make_trade(ticker: str, return_pct: float) -> BacktestTrade:
    signal_date = datetime(2026, 6, 10, 20, 0, 0, tzinfo=timezone.utc)
    return BacktestTrade(
        ticker=ticker,
        signal_date=signal_date,
        score=0.77,
        rules_passed=3,
        rules_total=4,
        buy_close=100.0,
        sell_date=signal_date + timedelta(days=7),
        sell_close=100.0 * (1 + return_pct / 100),
        return_pct=return_pct,
        win=return_pct > 0,
    )


def test_write_backtest_report_creates_pdf(tmp_path):
    trades = [
        _make_trade("AAPL", 5.2),   # win
        _make_trade("NVDA", -3.1),  # loss
        _make_trade("AMD", 1.0),    # win
    ]
    result = _make_result(trades)
    path = write_backtest_report(result, output_dir=tmp_path)

    assert path.exists()
    assert path.suffix == ".pdf"
    assert path.stat().st_size > 0
    with open(path, "rb") as f:
        assert f.read(4) == b"%PDF"
    assert path.name.startswith("backtest_")


def test_write_backtest_report_with_rule_attribution(tmp_path):
    trades = [
        _make_trade("AAPL", 5.2),
        _make_trade("NVDA", -3.1),
    ]
    attributions = [
        RuleAttribution(
            rule_name="near_52w_low",
            weight=1.5,
            passed_count=12,
            passed_win_rate=0.58,
            passed_avg_return_pct=1.9,
            failed_count=40,
            failed_win_rate=0.51,
            failed_avg_return_pct=0.6,
            edge_pct=1.3,
        ),
        RuleAttribution(
            rule_name="quality_uptrend",
            weight=1.0,
            passed_count=20,
            passed_win_rate=0.45,
            passed_avg_return_pct=-0.8,
            failed_count=32,
            failed_win_rate=0.56,
            failed_avg_return_pct=1.4,
            edge_pct=-2.2,  # negative edge -> exercises the red-coloring path
        ),
    ]
    result = _make_result(trades, rule_attribution=attributions)
    path = write_backtest_report(result, output_dir=tmp_path)

    assert path.exists()
    assert path.suffix == ".pdf"
    assert path.stat().st_size > 0
    with open(path, "rb") as f:
        assert f.read(4) == b"%PDF"
    assert path.name.startswith("backtest_")


def test_write_backtest_report_empty_trades(tmp_path):
    result = _make_result([])
    path = write_backtest_report(result, output_dir=tmp_path)
    assert path.exists()
    assert path.stat().st_size > 0
    with open(path, "rb") as f:
        assert f.read(4) == b"%PDF"
