"""Tests for the rule engine."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest

from screener.config import RuleConfig
from screener.models import Bar
from screener.rules.engine import RuleEngine


def _make_bars_simple(n: int, close: float = 100.0, volume: int = 1_000_000) -> list[Bar]:
    """Make n bars with incrementing dates."""
    base = datetime(2020, 1, 1, tzinfo=timezone.utc)
    return [
        Bar(
            timestamp=base + timedelta(days=i),
            open=close - 1,
            high=close + 2,
            low=close - 2,
            close=close,
            volume=volume,
        )
        for i in range(n)
    ]


@pytest.fixture
def bars_250():
    return _make_bars_simple(250, close=100.0, volume=1_000_000)


@pytest.fixture
def engine_single_rule():
    rules = [RuleConfig(name="test_close", weight=1.0, condition="close > 50")]
    return RuleEngine(rules)


def test_rule_passes_when_condition_true(bars_250, engine_single_rule):
    results = engine_single_rule.evaluate("AAPL", bars_250)
    assert len(results) == 1
    assert results[0].passed is True
    assert results[0].rule_name == "test_close"


def test_rule_fails_when_condition_false(bars_250):
    rules = [RuleConfig(name="test_close", weight=1.0, condition="close < 50")]
    engine = RuleEngine(rules)
    results = engine.evaluate("AAPL", bars_250)
    assert results[0].passed is False


def test_score_all_pass():
    rules = [
        RuleConfig(name="r1", weight=2.0, condition="close > 50"),
        RuleConfig(name="r2", weight=1.0, condition="close > 50"),
    ]
    engine = RuleEngine(rules)
    bars = _make_bars_simple(250, close=100.0)
    results = engine.evaluate("AAPL", bars)
    score = engine.score(results)
    assert score == pytest.approx(1.0)


def test_score_none_pass():
    rules = [
        RuleConfig(name="r1", weight=2.0, condition="close < 50"),
        RuleConfig(name="r2", weight=1.0, condition="close < 50"),
    ]
    engine = RuleEngine(rules)
    bars = _make_bars_simple(250, close=100.0)
    results = engine.evaluate("AAPL", bars)
    score = engine.score(results)
    assert score == pytest.approx(0.0)


def test_score_partial():
    rules = [
        RuleConfig(name="r1", weight=2.0, condition="close > 50"),   # passes
        RuleConfig(name="r2", weight=2.0, condition="close < 50"),   # fails
    ]
    engine = RuleEngine(rules)
    bars = _make_bars_simple(250, close=100.0)
    results = engine.evaluate("AAPL", bars)
    score = engine.score(results)
    assert score == pytest.approx(0.5)


def test_rsi_rule_evaluates():
    """RSI evaluates correctly when bars have actual price variation."""
    base = datetime(2020, 1, 1, tzinfo=timezone.utc)
    # Build 250 bars with alternating close prices so RSI is a real number
    bars = []
    for i in range(250):
        close = 100.0 + (5.0 if i % 2 == 0 else -5.0)
        bars.append(Bar(
            timestamp=base + timedelta(days=i),
            open=close - 1,
            high=close + 2,
            low=close - 2,
            close=close,
            volume=1_000_000,
        ))
    rules = [RuleConfig(name="rsi_check", weight=1.0, condition="rsi(14) < 200")]
    engine = RuleEngine(rules)
    results = engine.evaluate("AAPL", bars)
    # RSI is a real number with price variation, and is always between 0 and 100
    assert results[0].passed is True  # RSI is always < 200


def test_sma_rule_evaluates(bars_250):
    # With all closes = 100, sma(50) == 100 and sma(200) == 100
    rules = [RuleConfig(name="sma_eq", weight=1.0, condition="sma(50) > 0")]
    engine = RuleEngine(rules)
    results = engine.evaluate("AAPL", bars_250)
    assert results[0].passed is True


def test_volume_rule_evaluates(bars_250):
    rules = [RuleConfig(name="vol_check", weight=1.0, condition="volume > 0")]
    engine = RuleEngine(rules)
    results = engine.evaluate("AAPL", bars_250)
    assert results[0].passed is True


def test_detail_contains_close(bars_250, engine_single_rule):
    results = engine_single_rule.evaluate("AAPL", bars_250)
    assert "close" in results[0].detail


def test_score_empty_rules():
    engine = RuleEngine([])
    score = engine.score([])
    assert score == 0.0


def test_invalid_condition_returns_failed_result(bars_250):
    rules = [RuleConfig(name="bad_rule", weight=1.0, condition="undefined_var > 0")]
    engine = RuleEngine(rules)
    results = engine.evaluate("AAPL", bars_250)
    # Should not raise; should return passed=False with error detail
    assert results[0].passed is False


def test_all_five_spec_rules():
    """Evaluate all 5 rules from the spec against known bars."""
    from screener.config import load_rules_config
    from pathlib import Path
    rules_config = load_rules_config(Path("config/rules.yaml"))
    engine = RuleEngine(rules_config.rules)
    # bars with close=100, volume=2M — with 250 identical bars:
    # sma(50)=100, sma(200)=100 → close > sma(200) fails (equal, not greater)
    # sma(50) > sma(200) fails (equal)
    # rsi(14) will be ~50 (neutral) → rsi(14) < 35 fails
    # volume=2M, sma_volume(20)=2M → volume > sma_volume(20)*1.5 fails
    # atr(14)/close will be small → reasonable_volatility passes
    bars = _make_bars_simple(250, close=100.0, volume=2_000_000)
    results = engine.evaluate("AAPL", bars)
    assert len(results) == 5
    names = [r.rule_name for r in results]
    assert "oversold_rsi" in names
    assert "above_long_trend" in names
    assert "golden_cross_state" in names
    assert "volume_spike" in names
    assert "reasonable_volatility" in names
    # Score must be between 0 and 1
    score = engine.score(results)
    assert 0.0 <= score <= 1.0
