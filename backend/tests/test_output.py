"""Tests for the JSON output writer."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from screener.models import RuleResult, ScreenerRun, Signal
from screener.output.json_writer import write_run, _serialise


def _make_run() -> ScreenerRun:
    rule_results = [
        RuleResult(rule_name="oversold_rsi", passed=True, weight=2.0, detail={"rsi_14": 31.2}),
        RuleResult(rule_name="above_long_trend", passed=False, weight=1.5, detail={}),
    ]
    signal = Signal(
        ticker="AAPL",
        timestamp=datetime(2024, 1, 15, 20, 0, 0, tzinfo=timezone.utc),
        score=2.0 / 3.5,
        rules_passed=1,
        rules_total=2,
        rule_results=rule_results,
        snapshot={"close": 192.31, "volume": 54000000},
    )
    return ScreenerRun(
        run_timestamp=datetime(2024, 1, 15, 20, 1, 14, tzinfo=timezone.utc),
        universe="static",
        alert_threshold=0.70,
        signals=[signal],
    )


def test_write_run_creates_file(tmp_path):
    run = _make_run()
    path = write_run(run, output_dir=tmp_path)
    assert path.exists()
    assert path.suffix == ".json"


def test_write_run_valid_json(tmp_path):
    run = _make_run()
    path = write_run(run, output_dir=tmp_path)
    data = json.loads(path.read_text())
    assert "run_timestamp" in data
    assert "signals" in data
    assert "universe" in data
    assert "alert_threshold" in data


def test_serialise_schema_shape():
    run = _make_run()
    data = _serialise(run)
    assert data["universe"] == "static"
    assert data["alert_threshold"] == 0.70
    sig = data["signals"][0]
    assert sig["ticker"] == "AAPL"
    assert "score" in sig
    assert "rules_passed" in sig
    assert "rules_total" in sig
    assert "snapshot" in sig
    assert "rule_results" in sig


def test_timestamp_format_is_utc_z():
    run = _make_run()
    data = _serialise(run)
    assert data["run_timestamp"].endswith("Z")
    assert data["signals"][0]["timestamp"].endswith("Z")


def test_rule_results_schema():
    run = _make_run()
    data = _serialise(run)
    rr = data["signals"][0]["rule_results"][0]
    assert rr["rule_name"] == "oversold_rsi"
    assert rr["passed"] is True
    assert rr["weight"] == 2.0
    assert "detail" in rr


def test_output_dir_autocreated(tmp_path):
    run = _make_run()
    nested = tmp_path / "a" / "b" / "runs"
    path = write_run(run, output_dir=nested)
    assert path.exists()
