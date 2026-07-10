"""Technical indicator library — each function returns the latest scalar value."""
from __future__ import annotations

import pandas as pd
import pandas_ta as ta


def _close(df: pd.DataFrame) -> pd.Series:
    return df["close"].astype(float)


def _volume(df: pd.DataFrame) -> pd.Series:
    return df["volume"].astype(float)


def _low(df: pd.DataFrame) -> pd.Series:
    return df["low"].astype(float)


def _high(df: pd.DataFrame) -> pd.Series:
    return df["high"].astype(float)


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


def macd_line(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> float:
    """MACD line (fast EMA minus slow EMA) of close, latest scalar."""
    result = ta.macd(_close(df), fast=fast, slow=slow, signal=signal)
    return float(result[f"MACD_{fast}_{slow}_{signal}"].iloc[-1])


def macd_signal_line(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> float:
    """MACD signal line (EMA of the MACD line) of close, latest scalar."""
    result = ta.macd(_close(df), fast=fast, slow=slow, signal=signal)
    return float(result[f"MACDs_{fast}_{slow}_{signal}"].iloc[-1])


def macd_histogram(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> float:
    """MACD histogram (MACD line minus signal line) of close, latest scalar."""
    result = ta.macd(_close(df), fast=fast, slow=slow, signal=signal)
    return float(result[f"MACDh_{fast}_{slow}_{signal}"].iloc[-1])


def low_52w(df: pd.DataFrame, period: int = 252) -> float:
    """Rolling low over `period` bars (default ~52 weeks of trading days), latest scalar.

    Uses `min_periods=1` so tickers with less than `period` bars of history
    still return a real value (their all-time low) instead of NaN.
    """
    result = _low(df).rolling(window=period, min_periods=1).min()
    return float(result.iloc[-1])


def high_52w(df: pd.DataFrame, period: int = 252) -> float:
    """Rolling high over `period` bars (default ~52 weeks of trading days), latest scalar.

    Uses `min_periods=1` so tickers with less than `period` bars of history
    still return a real value (their all-time high) instead of NaN.
    """
    result = _high(df).rolling(window=period, min_periods=1).max()
    return float(result.iloc[-1])


def latest_close(df: pd.DataFrame) -> float:
    """Return the most recent close price."""
    return float(_close(df).iloc[-1])


def latest_volume(df: pd.DataFrame) -> int:
    """Return the most recent volume."""
    return int(_volume(df).iloc[-1])
