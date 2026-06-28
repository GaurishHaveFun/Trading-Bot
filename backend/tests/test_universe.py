"""Tests for universe providers."""
from __future__ import annotations
import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock
import pandas as pd
import pytest
from screener.universe.static import StaticUniverse
from screener.universe.sp500 import SP500Universe


UNIVERSE_YAML = Path(__file__).parent.parent / "config" / "universe.yaml"


# --- StaticUniverse tests ---

def test_static_universe_returns_10_symbols():
    u = StaticUniverse(UNIVERSE_YAML)
    symbols = u.get_symbols()
    assert len(symbols) == 10


def test_static_universe_contains_expected():
    u = StaticUniverse(UNIVERSE_YAML)
    symbols = u.get_symbols()
    assert "AAPL" in symbols
    assert "MSFT" in symbols
    assert "WMT" in symbols


def test_static_universe_returns_strings():
    u = StaticUniverse(UNIVERSE_YAML)
    for s in u.get_symbols():
        assert isinstance(s, str)


# --- SP500Universe tests ---

def _make_mock_df() -> pd.DataFrame:
    return pd.DataFrame({
        "Symbol": ["AAPL", "MSFT", "GOOGL", "BRK.B", "JPM"],
        "Security": ["Apple", "Microsoft", "Alphabet", "Berkshire", "JPMorgan"],
    })


def test_sp500_fetches_when_no_cache(tmp_path):
    with patch("screener.universe.sp500.pd.read_html", return_value=[_make_mock_df()]):
        u = SP500Universe(cache_dir=tmp_path)
        symbols = u.get_symbols()
    assert "AAPL" in symbols
    assert "BRK-B" in symbols  # dot replaced with dash
    assert len(symbols) == 5


def test_sp500_writes_cache(tmp_path):
    with patch("screener.universe.sp500.pd.read_html", return_value=[_make_mock_df()]):
        u = SP500Universe(cache_dir=tmp_path)
        u.get_symbols()
    cache_file = tmp_path / "sp500.json"
    assert cache_file.exists()
    data = json.loads(cache_file.read_text())
    assert isinstance(data, list)
    assert "AAPL" in data


def test_sp500_uses_cache_when_fresh(tmp_path):
    cached = ["AAPL", "TSLA", "NVDA"]
    cache_file = tmp_path / "sp500.json"
    cache_file.write_text(json.dumps(cached))
    # read_html should NOT be called
    with patch("screener.universe.sp500.pd.read_html") as mock_read:
        u = SP500Universe(cache_dir=tmp_path)
        symbols = u.get_symbols()
    mock_read.assert_not_called()
    assert symbols == cached


def test_sp500_refetches_when_cache_expired(tmp_path):
    cached = ["OLD"]
    cache_file = tmp_path / "sp500.json"
    cache_file.write_text(json.dumps(cached))
    # backdate the file modification time by 25 hours
    old_time = time.time() - 90000
    import os
    os.utime(cache_file, (old_time, old_time))

    with patch("screener.universe.sp500.pd.read_html", return_value=[_make_mock_df()]):
        u = SP500Universe(cache_dir=tmp_path)
        symbols = u.get_symbols()
    assert "AAPL" in symbols
    assert "OLD" not in symbols


@pytest.mark.integration
def test_sp500_real_fetch(tmp_path):
    """Live network test — skipped in CI with -m 'not integration'."""
    u = SP500Universe(cache_dir=tmp_path)
    symbols = u.get_symbols()
    assert len(symbols) > 400
    assert "AAPL" in symbols
