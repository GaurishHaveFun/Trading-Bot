"""Indicator function registry for the rule engine."""
from __future__ import annotations

import pandas as pd

from screener.indicators.library import (
    sma as _sma,
    ema as _ema,
    rsi as _rsi,
    atr as _atr,
    sma_volume as _sma_volume,
    low_52w as _low_52w,
    high_52w as _high_52w,
    macd_line as _macd_line,
    macd_signal_line as _macd_signal_line,
    macd_histogram as _macd_histogram,
)


def _make_sma(df: pd.DataFrame):
    def sma(period: int) -> float:
        return _sma(df, period)
    return sma


def _make_ema(df: pd.DataFrame):
    def ema(period: int) -> float:
        return _ema(df, period)
    return ema


def _make_rsi(df: pd.DataFrame):
    def rsi(period: int) -> float:
        return _rsi(df, period)
    return rsi


def _make_atr(df: pd.DataFrame):
    def atr(period: int) -> float:
        return _atr(df, period)
    return atr


def _make_sma_volume(df: pd.DataFrame):
    def sma_volume(period: int) -> float:
        return _sma_volume(df, period)
    return sma_volume


def _make_low_52w(df: pd.DataFrame):
    def low_52w(period: int = 252) -> float:
        return _low_52w(df, period)
    return low_52w


def _make_high_52w(df: pd.DataFrame):
    def high_52w(period: int = 252) -> float:
        return _high_52w(df, period)
    return high_52w


def _make_macd_line(df: pd.DataFrame):
    def macd_line(fast: int = 12, slow: int = 26, signal: int = 9) -> float:
        return _macd_line(df, fast, slow, signal)
    return macd_line


def _make_macd_signal_line(df: pd.DataFrame):
    def macd_signal_line(fast: int = 12, slow: int = 26, signal: int = 9) -> float:
        return _macd_signal_line(df, fast, slow, signal)
    return macd_signal_line


def _make_macd_histogram(df: pd.DataFrame):
    def macd_histogram(fast: int = 12, slow: int = 26, signal: int = 9) -> float:
        return _macd_histogram(df, fast, slow, signal)
    return macd_histogram


def build_symbol_table(
    df: pd.DataFrame,
    close: float,
    volume: float,
    meta: dict | None = None,
    in_watchlist: bool = False,
) -> dict:
    """Build the asteval symbol table for a single ticker's bar DataFrame.

    `meta` carries per-symbol quote metadata from the universe provider's
    `get_quotes()` (e.g. price_to_book, change_pct). Missing values fall back
    to safe defaults so rule conditions never blow up on absent data.
    """
    meta = meta or {}
    price_to_book = meta.get("price_to_book")
    if price_to_book is None:
        price_to_book = float("inf")
    change_pct = meta.get("change_pct")
    if change_pct is None:
        change_pct = 0.0
    industry = meta.get("industry") or ""
    is_chip = "semiconductor" in industry.lower()

    return {
        "sma": _make_sma(df),
        "ema": _make_ema(df),
        "rsi": _make_rsi(df),
        "atr": _make_atr(df),
        "sma_volume": _make_sma_volume(df),
        "low_52w": _make_low_52w(df),
        "high_52w": _make_high_52w(df),
        "macd_line": _make_macd_line(df),
        "macd_signal_line": _make_macd_signal_line(df),
        "macd_histogram": _make_macd_histogram(df),
        "close": close,
        "volume": volume,
        "price_to_book": price_to_book,
        "change_pct": change_pct,
        "in_watchlist": in_watchlist,
        "industry": industry,
        "is_chip": is_chip,
    }
