"""Tests for the PDF report writer."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from screener.models import RuleResult, ScreenerRun, Signal
from screener.output.pdf_writer import (
    _fmt_num,
    _fmt_pb,
    _fmt_pct,
    _fmt_vol,
    write_report,
    write_ticker_report,
)


def _make_signal(ticker: str, score: float, price_to_book: float = 4.2) -> Signal:
    rule_results = [
        RuleResult(rule_name="oversold_rsi", passed=True, weight=2.0, detail={"rsi_14": 31.2, "threshold": 35}),
        RuleResult(rule_name="above_long_trend", passed=False, weight=1.5, detail={}),
    ]
    return Signal(
        ticker=ticker,
        timestamp=datetime(2024, 1, 15, 20, 0, 0, tzinfo=timezone.utc),
        score=score,
        rules_passed=1,
        rules_total=2,
        rule_results=rule_results,
        snapshot={
            "close": 540.8800048828125,
            "volume": 27_754_556,
            "rsi_14": 31.2456,
            "sma_50": 188.4,
            "sma_200": 175.1,
            "atr_14": 4.2,
            "price_to_book": price_to_book,
            "change_pct": -6.891234,
            "in_watchlist": True,
        },
    )


def _make_run() -> ScreenerRun:
    signals = [
        _make_signal("AAPL", score=0.85),  # above threshold
        _make_signal("MSFT", score=0.40),  # below threshold
        _make_signal("XYZ", score=0.55, price_to_book=float("inf")),  # inf P/B
    ]
    return ScreenerRun(
        run_timestamp=datetime(2024, 1, 15, 20, 1, 14, tzinfo=timezone.utc),
        universe="static",
        alert_threshold=0.70,
        signals=signals,
    )


def test_write_report_creates_pdf(tmp_path):
    run = _make_run()
    path = write_report(run, output_dir=tmp_path)
    assert path.exists()
    assert path.suffix == ".pdf"
    assert path.stat().st_size > 0
    with open(path, "rb") as f:
        assert f.read(4) == b"%PDF"


def test_write_report_filename_uses_run_timestamp(tmp_path):
    run = _make_run()
    path = write_report(run, output_dir=tmp_path)
    assert path.name == "report_20240115T200114Z.pdf"


def test_write_ticker_report_creates_pdf(tmp_path):
    signal = _make_signal("NVDA", score=0.72)
    path = write_ticker_report(signal, alert_threshold=0.70, universe="static", output_dir=tmp_path)
    assert path.exists()
    assert path.suffix == ".pdf"
    assert path.stat().st_size > 0
    with open(path, "rb") as f:
        assert f.read(4) == b"%PDF"
    assert "NVDA" in path.name


def test_write_report_empty_signals(tmp_path):
    run = ScreenerRun(
        run_timestamp=datetime(2024, 1, 15, 20, 1, 14, tzinfo=timezone.utc),
        universe="static",
        alert_threshold=0.70,
        signals=[],
    )
    path = write_report(run, output_dir=tmp_path)
    assert path.exists()
    assert path.stat().st_size > 0


def test_fmt_num_rounds_to_two_decimals():
    assert _fmt_num(540.8800048828125) == "540.88"


def test_fmt_num_none_and_inf_render_as_dash():
    assert _fmt_num(None) == "—"
    assert _fmt_num(float("inf")) == "—"
    assert _fmt_num(float("nan")) == "—"


def test_fmt_pb_inf_renders_as_dash():
    assert _fmt_pb(float("inf")) == "—"
    assert _fmt_pb(4.2) == "4.20"


def test_fmt_pct_formats_with_percent_sign():
    assert _fmt_pct(-6.891234) == "-6.89%"
    assert _fmt_pct(70.0) == "70.00%"


def test_fmt_vol_humanizes_millions():
    assert _fmt_vol(27_754_556) == "27.75M"


def test_fmt_vol_humanizes_thousands_and_billions():
    assert _fmt_vol(1_500) == "1.50K"
    assert _fmt_vol(2_000_000_000) == "2.00B"
    assert _fmt_vol(500) == "500"
