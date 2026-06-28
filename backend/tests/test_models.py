"""Tests for Pydantic data models."""
from datetime import datetime, timezone

import pytest

from screener.models import Bar, RuleResult, ScreenerRun, Signal, Ticker


def test_bar_naive_timestamp_becomes_utc():
    bar = Bar(
        timestamp=datetime(2024, 1, 15, 16, 0, 0),
        open=100.0,
        high=105.0,
        low=99.0,
        close=103.0,
        volume=1000000,
    )
    assert bar.timestamp.tzinfo == timezone.utc


def test_bar_utc_timestamp_preserved():
    ts = datetime(2024, 1, 15, 21, 0, 0, tzinfo=timezone.utc)
    bar = Bar(timestamp=ts, open=100.0, high=105.0, low=99.0, close=103.0, volume=1000000)
    assert bar.timestamp == ts


def test_signal_score_valid():
    sig = Signal(
        ticker="AAPL",
        timestamp=datetime(2024, 1, 15, tzinfo=timezone.utc),
        score=0.85,
        rules_passed=4,
        rules_total=5,
        rule_results=[],
    )
    assert sig.score == 0.85


def test_signal_score_out_of_range():
    with pytest.raises(Exception):
        Signal(
            ticker="AAPL",
            timestamp=datetime(2024, 1, 15, tzinfo=timezone.utc),
            score=1.5,
            rules_passed=5,
            rules_total=5,
            rule_results=[],
        )


def test_rule_result_fields():
    r = RuleResult(rule_name="oversold_rsi", passed=True, weight=2.0, detail={"rsi_14": 31.2})
    assert r.passed is True
    assert r.weight == 2.0


def test_screener_run_defaults():
    run = ScreenerRun(
        run_timestamp=datetime(2024, 1, 15, tzinfo=timezone.utc),
        universe="static",
    )
    assert run.alert_threshold == 0.70
    assert run.signals == []
