"""Tests for SchwabFundamentalsProvider (Milestone 3)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from screener.data.schwab.fundamentals_provider import SchwabFundamentalsProvider
from screener.models import FundamentalsSnapshot


def _cache(get_return: FundamentalsSnapshot | None = None) -> MagicMock:
    cache = MagicMock()
    cache.get = MagicMock(return_value=get_return)
    cache.put = MagicMock()
    return cache


def _assert_all_none_zero_years(snap: FundamentalsSnapshot, symbol: str) -> None:
    assert snap.ticker == symbol
    assert snap.years_available == 0
    assert snap.fcf_5y_cumulative is None
    assert snap.interest_coverage is None
    assert snap.gross_margin is None
    assert snap.ocf_ni_ratio is None
    assert snap.net_margin is None
    assert snap.share_dilution_5y is None


async def test_fundamental_fields_present_still_all_none_no_exception():
    client = MagicMock()
    client.get = AsyncMock(
        return_value={
            "AAPL": {
                "assetMainType": "EQUITY",
                "quote": {"netPercentChangeInDouble": -1.5, "lastPriceInDouble": 190.0},
                "fundamental": {"peRatio": 30.0, "pbRatio": 40.0, "marketCap": 3e12},
                "reference": {"industry": "Consumer Electronics", "sector": "Technology"},
            }
        }
    )
    cache = _cache(get_return=None)
    provider = SchwabFundamentalsProvider(client=client, cache=cache)

    snap = await provider.get_fundamentals("AAPL")

    _assert_all_none_zero_years(snap, "AAPL")
    cache.put.assert_called_once()
    client.get.assert_awaited_once()


async def test_missing_symbol_key_in_response_same_all_none_no_exception():
    client = MagicMock()
    client.get = AsyncMock(return_value={})  # symbol missing entirely
    cache = _cache(get_return=None)
    provider = SchwabFundamentalsProvider(client=client, cache=cache)

    snap = await provider.get_fundamentals("ZZZZ")

    _assert_all_none_zero_years(snap, "ZZZZ")


async def test_non_dict_response_same_all_none_no_exception():
    client = MagicMock()
    client.get = AsyncMock(return_value=None)  # malformed payload
    cache = _cache(get_return=None)
    provider = SchwabFundamentalsProvider(client=client, cache=cache)

    snap = await provider.get_fundamentals("ZZZZ")

    _assert_all_none_zero_years(snap, "ZZZZ")


async def test_client_get_raises_still_returns_all_none_snapshot():
    client = MagicMock()
    client.get = AsyncMock(side_effect=RuntimeError("boom"))
    cache = _cache(get_return=None)
    provider = SchwabFundamentalsProvider(client=client, cache=cache)

    snap = await provider.get_fundamentals("BADSYM")

    _assert_all_none_zero_years(snap, "BADSYM")
    cache.put.assert_called_once()


async def test_cache_hit_skips_network_call_entirely():
    cached_snapshot = FundamentalsSnapshot(
        ticker="AAPL",
        as_of=datetime.now(timezone.utc),
        years_available=0,
        fcf_5y_cumulative=None,
        interest_coverage=None,
        gross_margin=None,
        ocf_ni_ratio=None,
        net_margin=None,
        share_dilution_5y=None,
    )
    client = MagicMock()
    client.get = AsyncMock()
    cache = _cache(get_return=cached_snapshot)
    provider = SchwabFundamentalsProvider(client=client, cache=cache)

    snap = await provider.get_fundamentals("AAPL")

    assert snap is cached_snapshot
    client.get.assert_not_called()
    cache.put.assert_not_called()
