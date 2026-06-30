"""Technical indicator library — each function returns the latest scalar value."""
from __future__ import annotations

import pandas as pd
import pandas_ta as ta


def _close(df: pd.DataFrame) -> pd.Series:
    return df["close"].astype(float)


def _volume(df: pd.DataFrame) -> pd.Series:
    return df["volume"].astype(float)


def sma(df: pd.DataFrame, period: int) -> float:
    """Simple moving average of close over `period` bars."""
    result = ta.sma(_close(df), length=period)
    return float(result.iloc[-1])


def ema(df: pd.DataFrame, period: int) -> float:
    """Exponential moving average of close over `period` bars."""
    result = ta.ema(_close(df), length=period)
    return float(result.iloc[-1])


def rsi(df: pd.DataFrame, period: int) -> float:
    """Relative Strength Index over `period` bars."""
    result = ta.rsi(_close(df), length=period)
    return float(result.iloc[-1])


def atr(df: pd.DataFrame, period: int) -> float:
    """Average True Range over `period` bars."""
    result = ta.atr(df["high"].astype(float), df["low"].astype(float), _close(df), length=period)
    return float(result.iloc[-1])


def sma_volume(df: pd.DataFrame, period: int) -> float:
    """Simple moving average of volume over `period` bars."""
    result = ta.sma(_volume(df), length=period)
    return float(result.iloc[-1])


def latest_close(df: pd.DataFrame) -> float:
    """Return the most recent close price."""
    return float(_close(df).iloc[-1])


def latest_volume(df: pd.DataFrame) -> int:
    """Return the most recent volume."""
    return int(_volume(df).iloc[-1])
