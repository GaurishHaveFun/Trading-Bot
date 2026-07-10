"""Tests for the indicator library against the aapl_sample.csv fixture."""
from __future__ import annotations

from pathlib import Path
import pandas as pd
import numpy as np
import pytest

from screener.indicators.library import (
    sma,
    ema,
    rsi,
    atr,
    sma_volume,
    latest_close,
    latest_volume,
    low_52w,
    high_52w,
    macd_line,
    macd_signal_line,
    macd_histogram,
)

FIXTURE = Path(__file__).parent / "fixtures" / "aapl_sample.csv"


@pytest.fixture(scope="module")
def df():
    frame = pd.read_csv(FIXTURE)
    return frame


def test_sma_50(df):
    result = sma(df, 50)
    expected = df["close"].rolling(50).mean().iloc[-1]
    assert result == pytest.approx(expected, rel=1e-4)


def test_sma_200(df):
    result = sma(df, 200)
    expected = df["close"].rolling(200).mean().iloc[-1]
    assert result == pytest.approx(expected, rel=1e-4)


def test_ema_20(df):
    result = ema(df, 20)
    # pandas-ta EMA uses same formula as pandas ewm with adjust=False
    expected = df["close"].ewm(span=20, adjust=False).mean().iloc[-1]
    assert result == pytest.approx(expected, rel=1e-3)


def test_rsi_14_in_range(df):
    result = rsi(df, 14)
    assert 0 <= result <= 100


def test_atr_14_positive(df):
    result = atr(df, 14)
    assert result > 0


def test_sma_volume_20(df):
    result = sma_volume(df, 20)
    expected = df["volume"].astype(float).rolling(20).mean().iloc[-1]
    assert result == pytest.approx(expected, rel=1e-4)


def test_latest_close(df):
    result = latest_close(df)
    assert result == pytest.approx(df["close"].iloc[-1], rel=1e-6)


def test_latest_volume(df):
    result = latest_volume(df)
    assert result == df["volume"].iloc[-1]


def test_sma_returns_float(df):
    assert isinstance(sma(df, 50), float)


def test_latest_volume_returns_int(df):
    assert isinstance(latest_volume(df), int)


# --- low_52w / high_52w ---

def test_low_52w_matches_rolling_min(df):
    # fixture has 250 rows; use a period within range for a direct comparison
    result = low_52w(df, period=200)
    expected = df["low"].rolling(200, min_periods=1).min().iloc[-1]
    assert result == pytest.approx(expected, rel=1e-6)


def test_high_52w_matches_rolling_max(df):
    result = high_52w(df, period=200)
    expected = df["high"].rolling(200, min_periods=1).max().iloc[-1]
    assert result == pytest.approx(expected, rel=1e-6)


def test_low_52w_returns_float(df):
    assert isinstance(low_52w(df, period=50), float)


def test_high_52w_returns_float(df):
    assert isinstance(high_52w(df, period=50), float)


def test_low_52w_never_nan_when_period_exceeds_available_bars(df):
    """Default period=252 exceeds the 250-row fixture — must not return NaN."""
    result = low_52w(df)
    assert result == result  # NaN != NaN, so this fails if NaN
    assert result > 0


def test_high_52w_never_nan_when_period_exceeds_available_bars(df):
    result = high_52w(df)
    assert result == result  # NaN check
    assert result > 0


def test_low_52w_le_high_52w(df):
    assert low_52w(df, period=200) <= high_52w(df, period=200)


# --- macd_line / macd_signal_line / macd_histogram ---
# Uses the aapl_sample.csv fixture (real varying prices) — MACD needs price
# movement to be meaningful, unlike a flat-price fixture.

def test_macd_line_returns_finite_float(df):
    result = macd_line(df)
    assert isinstance(result, float)
    assert result == result  # NaN check (NaN != NaN)


def test_macd_signal_line_returns_finite_float(df):
    result = macd_signal_line(df)
    assert isinstance(result, float)
    assert result == result  # NaN check


def test_macd_histogram_returns_finite_float(df):
    result = macd_histogram(df)
    assert isinstance(result, float)
    assert result == result  # NaN check


def test_macd_histogram_equals_line_minus_signal(df):
    """Definitional property of MACD: histogram = line - signal."""
    assert macd_histogram(df) == pytest.approx(macd_line(df) - macd_signal_line(df))
