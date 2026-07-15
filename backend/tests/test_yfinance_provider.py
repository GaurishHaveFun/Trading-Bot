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


def _make_fixed_datetime(fixed_now: datetime):
    """Return a datetime subclass whose .now() is pinned to fixed_now while
    normal construction (datetime(y, m, d, ...)) still behaves like datetime.
    Used to control what `get_bars` considers "today" without breaking the
    module's own `datetime(...)` construction calls."""

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now.astimezone(tz) if tz else fixed_now

    return _FixedDateTime


async def test_refreshes_stale_today_bar(provider, cache):
    """A same-day cache hit must not pin a stale pre-close price all day —
    the corrected bar should overwrite the provisional one via put_replace."""
    fake_today = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    # Seed the full requested range (Jan 10-15) so head/tail backfill never
    # fires — only the "today" row (Jan 15) is stale/provisional.
    bars_in = [_make_bar(d) for d in range(10, 15)] + [_make_bar(15, close=100.0)]
    cache.put("AAPL", bars_in)

    corrected_bar = _make_bar(15, close=124.0)

    with patch("screener.data.yfinance_provider.datetime", _make_fixed_datetime(fake_today)):
        with patch.object(provider, "_download", return_value=[corrected_bar]) as mock_dl:
            bars = await provider.get_bars(
                "AAPL",
                datetime(2024, 1, 10, tzinfo=timezone.utc),
                datetime(2024, 1, 15, tzinfo=timezone.utc),
            )

    mock_dl.assert_called_once()
    today_bars = [b for b in bars if b.timestamp.date() == fake_today.date()]
    assert len(today_bars) == 1
    assert today_bars[0].close == pytest.approx(124.0)

    # Cache itself must reflect the overwrite, not just the returned bars.
    cached = cache.get(
        "AAPL",
        datetime(2024, 1, 15, tzinfo=timezone.utc),
        datetime(2024, 1, 15, tzinfo=timezone.utc),
    )
    assert len(cached) == 1
    assert cached[0].close == pytest.approx(124.0)


async def test_historical_range_does_not_trigger_refresh_fetch(provider, cache):
    """Backtests query past date ranges and must never trigger a live refetch
    of a "today" bar — end being in the past means the guard must not fire."""
    fake_today = datetime(2024, 6, 1, tzinfo=timezone.utc)  # well after the Jan 2024 range
    bars_in = [_make_bar(d) for d in range(1, 11)]
    cache.put("AAPL", bars_in)

    with patch("screener.data.yfinance_provider.datetime", _make_fixed_datetime(fake_today)):
        with patch.object(provider, "_download") as mock_dl:
            bars = await provider.get_bars("AAPL", START, END)

    mock_dl.assert_not_called()
    assert len(bars) == 10


@pytest.mark.integration
async def test_real_aapl_refresh_not_pinned(tmp_path):
    """Live network test — calling get_bars twice in the same run for a range
    covering today should hit the network again for today's bar rather than
    silently reusing a stale cached row. Skipped in CI with -m 'not integration'."""
    cache = BarCache(db_path=tmp_path / "bars.db")
    provider = YFinanceProvider(cache=cache)
    now = datetime.now(timezone.utc)
    start = now.replace(day=1) if now.day > 5 else now
    with patch.object(provider, "_download", wraps=provider._download) as mock_dl:
        await provider.get_bars("AAPL", start, now)
        first_calls = mock_dl.call_count
        await provider.get_bars("AAPL", start, now)
        second_calls = mock_dl.call_count
    # The second call must still reach the network to refresh today's bar
    # (rather than short-circuiting entirely on a same-day cache hit).
    assert second_calls > first_calls
    cache.close()
