"""Tests for SchwabProvider."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from screener.data.cache import BarCache
from screener.data.schwab.bars_provider import SchwabProvider
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


def _make_candle(
    day: int,
    open_: float = 100.0,
    high: float = 105.0,
    low: float = 98.0,
    close: float = 102.0,
    volume: int = 1_000_000,
) -> dict:
    ts = datetime(2024, 1, day, 21, 0, 0, tzinfo=timezone.utc)
    return {
        "datetime": int(ts.timestamp() * 1000),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _make_payload(days: list[int]) -> dict:
    return {"candles": [_make_candle(d) for d in days]}


START = datetime(2024, 1, 1, tzinfo=timezone.utc)
END = datetime(2024, 1, 10, tzinfo=timezone.utc)


@pytest.fixture
def cache(tmp_path):
    c = BarCache(db_path=tmp_path / "bars.db")
    yield c
    c.close()


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.get = AsyncMock(return_value={"candles": []})
    return client


@pytest.fixture
def provider(mock_client, cache):
    return SchwabProvider(client=mock_client, cache=cache)


# ---------------------------------------------------------------------------
# Candle -> Bar conversion
# ---------------------------------------------------------------------------


async def test_candles_convert_to_bars_with_correct_utc_values(provider, mock_client):
    mock_client.get.return_value = _make_payload([1, 2, 3])
    bars = await provider.get_bars("AAPL", START, END)

    assert len(bars) == 3
    assert bars[0].timestamp.tzinfo == timezone.utc
    assert bars[0].open == pytest.approx(100.0)
    assert bars[0].high == pytest.approx(105.0)
    assert bars[0].low == pytest.approx(98.0)
    assert bars[0].close == pytest.approx(102.0)
    assert bars[0].volume == 1_000_000


async def test_candle_with_none_field_is_dropped(provider, mock_client):
    good = _make_candle(1)
    bad = _make_candle(2)
    bad["close"] = None
    mock_client.get.return_value = {"candles": [good, bad]}

    bars = await provider.get_bars("AAPL", START, END)

    assert len(bars) == 1
    assert bars[0].timestamp.day == 1


async def test_candle_with_nan_field_is_dropped(provider, mock_client):
    good = _make_candle(1)
    bad = _make_candle(2)
    bad["high"] = float("nan")
    mock_client.get.return_value = {"candles": [good, bad]}

    bars = await provider.get_bars("AAPL", START, END)

    assert len(bars) == 1
    assert bars[0].timestamp.day == 1


async def test_missing_candles_key_returns_empty(provider, mock_client):
    mock_client.get.return_value = {}
    bars = await provider.get_bars("AAPL", START, END)
    assert bars == []


# ---------------------------------------------------------------------------
# Cache integration
# ---------------------------------------------------------------------------


async def test_fetches_and_caches_when_empty(provider, mock_client, cache):
    mock_client.get.return_value = _make_payload([1, 2, 3, 4, 5])

    bars = await provider.get_bars("AAPL", START, END)
    assert len(bars) == 5

    cached = cache.get("AAPL", START, END)
    assert len(cached) == 5


async def test_returns_from_cache_without_fetching(provider, mock_client, cache):
    bars_in = [_make_bar(d) for d in range(1, 11)]
    cache.put("AAPL", bars_in)

    bars = await provider.get_bars("AAPL", START, END)

    mock_client.get.assert_not_called()
    assert len(bars) == 10


async def test_empty_response_returns_empty_list(provider, mock_client):
    mock_client.get.return_value = {"candles": []}
    bars = await provider.get_bars("AAPL", START, END)
    assert bars == []


# ---------------------------------------------------------------------------
# Same-day refresh / historical-range guard
# ---------------------------------------------------------------------------


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


async def test_refreshes_stale_today_bar(provider, mock_client, cache):
    """A same-day cache hit must not pin a stale pre-close price all day —
    the corrected bar should overwrite the provisional one via put_replace."""
    fake_today = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    # Seed the full requested range (Jan 10-15) so head/tail backfill never
    # fires — only the "today" row (Jan 15) is stale/provisional.
    bars_in = [_make_bar(d) for d in range(10, 15)] + [_make_bar(15, close=100.0)]
    cache.put("AAPL", bars_in)

    mock_client.get.return_value = {"candles": [_make_candle(15, close=124.0)]}

    with patch("screener.data.schwab.bars_provider.datetime", _make_fixed_datetime(fake_today)):
        bars = await provider.get_bars(
            "AAPL",
            datetime(2024, 1, 10, tzinfo=timezone.utc),
            datetime(2024, 1, 15, tzinfo=timezone.utc),
        )

    mock_client.get.assert_called_once()
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


async def test_historical_range_does_not_trigger_refresh_fetch(provider, mock_client, cache):
    """Backtests query past date ranges and must never trigger a live refetch
    of a "today" bar — end being in the past means the guard must not fire."""
    fake_today = datetime(2024, 6, 1, tzinfo=timezone.utc)  # well after the Jan 2024 range
    bars_in = [_make_bar(d) for d in range(1, 11)]
    cache.put("AAPL", bars_in)

    with patch("screener.data.schwab.bars_provider.datetime", _make_fixed_datetime(fake_today)):
        bars = await provider.get_bars("AAPL", START, END)

    mock_client.get.assert_not_called()
    assert len(bars) == 10


# ---------------------------------------------------------------------------
# Integration (live network) — skipped without real credentials
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_real_aapl_fetch(tmp_path):
    """Live network test — skipped in CI with -m 'not integration'. Requires
    SCHWAB_APP_KEY to be configured (and a valid persisted token from a prior
    --auth-schwab run)."""
    from screener.config import get_settings
    from screener.data.schwab.auth import SchwabAuth
    from screener.data.schwab.client import SchwabClient

    settings = get_settings()
    if not settings.schwab_app_key:
        pytest.skip("SCHWAB_APP_KEY not configured")

    auth = SchwabAuth(
        app_key=settings.schwab_app_key,
        app_secret=settings.schwab_app_secret,
        callback_url=settings.schwab_callback_url,
        token_path=settings.schwab_token_path,
    )
    client = SchwabClient(auth=auth)
    cache = BarCache(db_path=tmp_path / "bars.db")
    provider = SchwabProvider(client=client, cache=cache)

    bars = await provider.get_bars(
        "AAPL",
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 1, 31, tzinfo=timezone.utc),
    )
    assert len(bars) > 10
    for bar in bars:
        assert bar.timestamp.tzinfo == timezone.utc
        assert bar.close > 0

    await client.aclose()
    cache.close()
