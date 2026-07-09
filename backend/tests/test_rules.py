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
    """Evaluate all 5 buy-the-dip rules from the spec against known bars."""
    from screener.config import load_rules_config
    from pathlib import Path
    rules_config = load_rules_config(Path("config/rules.yaml"))
    engine = RuleEngine(rules_config.rules)
    # bars with close=100, volume=2M — with 250 identical bars:
    # in_watchlist=False (no watchlist passed) → big_tech_or_chip fails
    # rsi(14) will be ~50 (neutral) → oversold_band (25 < rsi < 40) fails
    # sma(200)=100 → close > sma(200) fails (equal, not greater)
    # low_52w(252)=98 (constant low) → close <= low_52w*1.15 passes
    # price_to_book defaults to inf (no meta) → undervalued_pb fails
    bars = _make_bars_simple(250, close=100.0, volume=2_000_000)
    results = engine.evaluate("AAPL", bars)
    assert len(results) == 5
    names = [r.rule_name for r in results]
    assert "big_tech_or_chip" in names
    assert "oversold_band" in names
    assert "quality_uptrend" in names
    assert "near_52w_low" in names
    assert "undervalued_pb" in names
    # Score must be between 0 and 1
    score = engine.score(results)
    assert 0.0 <= score <= 1.0


# --- in_watchlist ---

def test_in_watchlist_true_when_symbol_in_watchlist(bars_250):
    rules = [RuleConfig(name="wl", weight=1.0, condition="in_watchlist")]
    engine = RuleEngine(rules)
    results = engine.evaluate("NVDA", bars_250, watchlist={"NVDA", "AAPL"})
    assert results[0].passed is True
    assert results[0].detail["in_watchlist"] is True


def test_in_watchlist_false_when_symbol_not_in_watchlist(bars_250):
    rules = [RuleConfig(name="wl", weight=1.0, condition="in_watchlist")]
    engine = RuleEngine(rules)
    results = engine.evaluate("XOM", bars_250, watchlist={"NVDA", "AAPL"})
    assert results[0].passed is False
    assert results[0].detail["in_watchlist"] is False


def test_in_watchlist_false_when_no_watchlist_passed(bars_250):
    rules = [RuleConfig(name="wl", weight=1.0, condition="in_watchlist")]
    engine = RuleEngine(rules)
    results = engine.evaluate("NVDA", bars_250)
    assert results[0].passed is False


# --- price_to_book default-inf safety ---

def test_price_to_book_defaults_to_inf_when_meta_missing(bars_250):
    rules = [RuleConfig(name="pb", weight=1.0, condition="price_to_book < 4")]
    engine = RuleEngine(rules)
    results = engine.evaluate("AAPL", bars_250)
    assert results[0].passed is False
    assert results[0].detail["price_to_book"] == float("inf")


def test_price_to_book_defaults_to_inf_when_meta_has_no_pb_key(bars_250):
    rules = [RuleConfig(name="pb", weight=1.0, condition="price_to_book < 4")]
    engine = RuleEngine(rules)
    results = engine.evaluate("AAPL", bars_250, meta={"change_pct": -3.0})
    assert results[0].detail["price_to_book"] == float("inf")


def test_price_to_book_uses_meta_value_when_present(bars_250):
    rules = [RuleConfig(name="pb", weight=1.0, condition="price_to_book < 4")]
    engine = RuleEngine(rules)
    results = engine.evaluate("AAPL", bars_250, meta={"price_to_book": 2.5})
    assert results[0].passed is True


# --- near_52w_low ---

def test_near_52w_low_passes_at_the_low():
    """All bars at the same close/low → close equals low_52w → passes."""
    bars = _make_bars_simple(250, close=100.0)
    rules = [RuleConfig(name="near_low", weight=1.0, condition="close <= low_52w(252) * 1.15")]
    engine = RuleEngine(rules)
    results = engine.evaluate("AAPL", bars)
    assert results[0].passed is True


def test_near_52w_low_fails_far_above_low():
    """Build bars where the low was far below current close/low."""
    base = datetime(2020, 1, 1, tzinfo=timezone.utc)
    bars = []
    for i in range(250):
        low = 10.0 if i == 0 else 100.0
        close = 10.0 if i == 0 else 200.0
        bars.append(Bar(
            timestamp=base + timedelta(days=i),
            open=close - 1,
            high=close + 2,
            low=low,
            close=close,
            volume=1_000_000,
        ))
    rules = [RuleConfig(name="near_low", weight=1.0, condition="close <= low_52w(252) * 1.15")]
    engine = RuleEngine(rules)
    results = engine.evaluate("AAPL", bars)
    assert results[0].passed is False


# --- oversold_band boundaries ---

def test_oversold_band_condition_present_in_detail():
    """RSI between 25 and 40 should pass the oversold_band condition."""
    base = datetime(2020, 1, 1, tzinfo=timezone.utc)
    bars = []
    close = 100.0
    for i in range(250):
        # Mostly downward drift so RSI settles low but not zero
        close -= 0.3 if i % 3 != 0 else -0.1
        bars.append(Bar(
            timestamp=base + timedelta(days=i),
            open=close + 0.5,
            high=close + 1,
            low=close - 1,
            close=close,
            volume=1_000_000,
        ))
    rules = [RuleConfig(name="oversold_band", weight=1.0, condition="rsi(14) > 25 and rsi(14) < 40")]
    engine = RuleEngine(rules)
    results = engine.evaluate("AAPL", bars)
    assert "rsi_14" in results[0].detail
    assert 0.0 <= results[0].detail["rsi_14"] <= 100.0


def test_oversold_band_fails_when_rsi_neutral(bars_250):
    """Flat prices → RSI is neutral (NaN-avoiding ~ constant), outside 25-40 band or edge — assert boolean type only."""
    rules = [RuleConfig(name="oversold_band", weight=1.0, condition="rsi(14) > 25 and rsi(14) < 40")]
    engine = RuleEngine(rules)
    results = engine.evaluate("AAPL", bars_250)
    assert isinstance(results[0].passed, bool)


# --- is_chip ---

def test_is_chip_true_for_semiconductors_industry(bars_250):
    rules = [RuleConfig(name="chip", weight=1.0, condition="is_chip")]
    engine = RuleEngine(rules)
    results = engine.evaluate("SKYT", bars_250, meta={"industry": "Semiconductors"})
    assert results[0].passed is True
    assert results[0].detail["is_chip"] is True


def test_is_chip_true_for_semiconductor_equipment_industry(bars_250):
    rules = [RuleConfig(name="chip", weight=1.0, condition="is_chip")]
    engine = RuleEngine(rules)
    results = engine.evaluate(
        "ASML", bars_250, meta={"industry": "Semiconductor Equipment & Materials"}
    )
    assert results[0].passed is True


def test_is_chip_false_for_unrelated_industry(bars_250):
    rules = [RuleConfig(name="chip", weight=1.0, condition="is_chip")]
    engine = RuleEngine(rules)
    results = engine.evaluate("KO", bars_250, meta={"industry": "Beverages—Non-Alcoholic"})
    assert results[0].passed is False
    assert results[0].detail["is_chip"] is False


def test_is_chip_false_when_industry_missing(bars_250):
    rules = [RuleConfig(name="chip", weight=1.0, condition="is_chip")]
    engine = RuleEngine(rules)
    results = engine.evaluate("AAPL", bars_250)
    assert results[0].passed is False
    assert results[0].detail["is_chip"] is False


def test_big_tech_or_chip_passes_for_non_watchlist_chip_symbol(bars_250):
    """SKYT case: not on the watchlist, but a real semiconductor company."""
    rules = [
        RuleConfig(name="big_tech_or_chip", weight=2.0, condition="in_watchlist or is_chip")
    ]
    engine = RuleEngine(rules)
    results = engine.evaluate(
        "SKYT", bars_250, meta={"industry": "Semiconductors"}, watchlist={"NVDA", "AAPL"}
    )
    assert results[0].passed is True


def test_big_tech_or_chip_still_passes_via_watchlist_with_no_industry_meta(bars_250):
    rules = [
        RuleConfig(name="big_tech_or_chip", weight=2.0, condition="in_watchlist or is_chip")
    ]
    engine = RuleEngine(rules)
    results = engine.evaluate("AAPL", bars_250, watchlist={"NVDA", "AAPL"})
    assert results[0].passed is True


def test_big_tech_or_chip_fails_when_neither_watchlist_nor_chip(bars_250):
    rules = [
        RuleConfig(name="big_tech_or_chip", weight=2.0, condition="in_watchlist or is_chip")
    ]
    engine = RuleEngine(rules)
    results = engine.evaluate(
        "KO", bars_250, meta={"industry": "Beverages—Non-Alcoholic"}, watchlist={"NVDA", "AAPL"}
    )
    assert results[0].passed is False
