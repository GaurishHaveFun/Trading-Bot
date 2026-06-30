"""Tests for the indicator library against the aapl_sample.csv fixture."""
from __future__ import annotations

from pathlib import Path
import pandas as pd
import numpy as np
import pytest

from screener.indicators.library import sma, ema, rsi, atr, sma_volume, latest_close, latest_volume

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
