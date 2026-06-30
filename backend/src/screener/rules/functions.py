"""Indicator function registry for the rule engine."""
from __future__ import annotations

import pandas as pd

from screener.indicators.library import (
    sma as _sma,
    ema as _ema,
    rsi as _rsi,
    atr as _atr,
    sma_volume as _sma_volume,
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


def build_symbol_table(df: pd.DataFrame, close: float, volume: float) -> dict:
    """Build the asteval symbol table for a single ticker's bar DataFrame."""
    return {
        "sma": _make_sma(df),
        "ema": _make_ema(df),
        "rsi": _make_rsi(df),
        "atr": _make_atr(df),
        "sma_volume": _make_sma_volume(df),
        "close": close,
        "volume": volume,
    }
