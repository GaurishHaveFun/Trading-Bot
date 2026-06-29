"""Tests for YFinanceProvider."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch
import pandas as pd
import pytest

from screener.data.cache import BarCache
from screener.data.yfinance_provider import YFinanceProvider
from screener.models import Bar


def _make_bar(day: int, close: float = 150.0) -> Bar:
    return Bar(
        timestamp=datetime(2024, 1, day, 21, 0, 0, tzinfo=timezone.utc),
        open=close - 1,
        high=close + 2,
        low=close - 2,
        close=close,
        volume=1_000_000,
    )


def _make_df(days: list[int]) -> pd.DataFrame:
    """Build a fake yfinance DataFrame."""
    index = pd.to_datetime([f"2024-01-{d:02d}" for d in days], utc=True)
    return pd.DataFrame(
        {
            "Open": [100.0] * len(days),
            "High": [105.0] * len(days),
            "Low": [98.0] * len(days),
            "Close": [102.0] * len(days),
            "Volume": [1_000_000] * len(days),
        },
        index=index,
    )


START = datetime(2024, 1, 1, tzinfo=timezone.utc)
END = datetime(2024, 1, 10, tzinfo=timezone.utc)


@pytest.fixture
def cache(tmp_path):
    c = BarCache(db_path=tmp_path / "bars.db")
    yield c
    c.close()


@pytest.fixture
def provider(cache):
    return YFinanceProvider(cache=cache)


async def test_fetches_and_caches_when_empty(provider, cache):
    with patch.object(provider, "_download", return_value=[_make_bar(d) for d in [1, 2, 3, 4, 5]]):
        bars = await provider.get_bars("AAPL", START, END)
    assert len(bars) == 5
    # verify written to cache
    cached = cache.get("AAPL", START, END)
    assert len(cached) == 5


async def test_returns_from_cache_without_fetching(provider, cache):
    # Pre-populate cache covering the full queried range (Jan 1–10)
    bars_in = [_make_bar(d) for d in range(1, 11)]
    cache.put("AAPL", bars_in)

    with patch.object(provider, "_download") as mock_dl:
        bars = await provider.get_bars("AAPL", START, END)

    mock_dl.assert_not_called()
    assert len(bars) == 10


async def test_bars_have_utc_timestamps(provider):
    with patch.object(provider, "_download", return_value=[_make_bar(1)]):
        bars = await provider.get_bars("AAPL", START, END)
    assert bars[0].timestamp.tzinfo == timezone.utc


async def test_empty_response_returns_empty_list(provider):
    with patch.object(provider, "_download", return_value=[]):
        bars = await provider.get_bars("AAPL", START, END)
    assert bars == []


@pytest.mark.integration
async def test_real_aapl_fetch(tmp_path):
    """Live network test — skipped in CI with -m 'not integration'."""
    cache = BarCache(db_path=tmp_path / "bars.db")
    provider = YFinanceProvider(cache=cache)
    bars = await provider.get_bars(
        "AAPL",
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 1, 31, tzinfo=timezone.utc),
    )
    assert len(bars) > 10
    for bar in bars:
        assert bar.timestamp.tzinfo == timezone.utc
        assert bar.close > 0
    cache.close()
