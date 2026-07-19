"""Tests for SchwabFundamentalsProvider (Milestone 3; live-verified endpoint
switch — quotes -> instruments/fundamental — see module docstring)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from screener.data.schwab.fundamentals_provider import SchwabFundamentalsProvider
from screener.models import FundamentalsSnapshot


def _cache(get_return: FundamentalsSnapshot | None = None) -> MagicMock:
    cache = MagicMock()
    cache.get = MagicMock(return_value=get_return)
    cache.put = MagicMock()
    return cache


def _client(call_return=None, call_side_effect=None) -> MagicMock:
    """Build a mock SchwabClient whose `.call()` is an AsyncMock, and whose
    `.raw.Instrument.Projection.FUNDAMENTAL` / `.raw.get_instruments` exist
    (as plain MagicMocks — `call()` is what's actually awaited/asserted;
    `get_instruments` itself is never directly invoked by the mock, since
    `_fetch_fundamental` passes a lambda into `client.call()`)."""
    client = MagicMock()
    if call_side_effect is not None:
        client.call = AsyncMock(side_effect=call_side_effect)
    else:
        client.call = AsyncMock(return_value=call_return)
    return client


def _assert_all_none_zero_years(snap: FundamentalsSnapshot, symbol: str) -> None:
    assert snap.ticker == symbol
    assert snap.years_available == 0
    assert snap.fcf_5y_cumulative is None
    assert snap.interest_coverage is None
    assert snap.gross_margin is None
    assert snap.ocf_ni_ratio is None
    assert snap.net_margin is None
    assert snap.share_dilution_5y is None


async def test_fundamental_fields_present_populate_the_three_ttm_metrics_with_pct_conversion():
    """Real Schwab schema (live-confirmed): the instruments/fundamental
    endpoint's response envelope is a top-level `instruments` list, each
    entry's `fundamental` dict carries `interestCoverage`/`grossMarginTTM`/
    `netProfitMarginTTM` as real TTM values, with the two margins expressed
    as PERCENTAGES (live-confirmed against real AAPL/MSFT data) — so they
    must be divided by 100 here. `interest_coverage` is NOT rescaled."""
    client = _client(
        call_return={
            "instruments": [
                {
                    "symbol": "AAPL",
                    "cusip": "037833100",
                    "assetType": "EQUITY",
                    "fundamental": {
                        "symbol": "AAPL",
                        "peRatio": 30.0,
                        "pbRatio": 40.0,
                        "marketCap": 3e12,
                        "interestCoverage": 12.5,
                        "grossMarginTTM": 43.0,
                        "netProfitMarginTTM": 25.0,
                    },
                }
            ]
        }
    )
    cache = _cache(get_return=None)
    provider = SchwabFundamentalsProvider(client=client, cache=cache)

    snap = await provider.get_fundamentals("AAPL")

    assert snap.ticker == "AAPL"
    assert snap.years_available == 0
    assert snap.interest_coverage == 12.5
    assert snap.gross_margin == 0.43
    assert snap.net_margin == 0.25
    assert snap.fcf_5y_cumulative is None
    assert snap.ocf_ni_ratio is None
    assert snap.share_dilution_5y is None
    cache.put.assert_called_once()
    client.call.assert_awaited_once()


async def test_fundamental_fields_absent_leave_ttm_metrics_none():
    client = _client(
        call_return={
            "instruments": [
                {
                    "symbol": "AAPL",
                    "assetType": "EQUITY",
                    "fundamental": {"peRatio": 30.0, "pbRatio": 40.0, "marketCap": 3e12},
                }
            ]
        }
    )
    cache = _cache(get_return=None)
    provider = SchwabFundamentalsProvider(client=client, cache=cache)

    snap = await provider.get_fundamentals("AAPL")

    _assert_all_none_zero_years(snap, "AAPL")


async def test_malformed_ttm_fields_stay_none_independently_no_exception():
    """A non-numeric string for one field must not raise and must not affect
    the other two fields."""
    client = _client(
        call_return={
            "instruments": [
                {
                    "symbol": "AAPL",
                    "assetType": "EQUITY",
                    "fundamental": {
                        "interestCoverage": "not-a-number",
                        "grossMarginTTM": 50.0,
                        "netProfitMarginTTM": None,
                    },
                }
            ]
        }
    )
    cache = _cache(get_return=None)
    provider = SchwabFundamentalsProvider(client=client, cache=cache)

    snap = await provider.get_fundamentals("AAPL")

    assert snap.interest_coverage is None
    assert snap.gross_margin == 0.5
    assert snap.net_margin is None
    assert snap.years_available == 0
    assert snap.fcf_5y_cumulative is None
    assert snap.ocf_ni_ratio is None
    assert snap.share_dilution_5y is None


async def test_interest_coverage_exactly_zero_logs_suspicious_warning():
    """Live-observed on both real AAPL and MSFT responses this session:
    `interestCoverage: 0.0`. That value is still populated (not dropped/
    converted) but must trigger a structured warning flagging it as
    suspicious, per the module docstring's caveat."""
    client = _client(
        call_return={
            "instruments": [
                {
                    "symbol": "AAPL",
                    "assetType": "EQUITY",
                    "fundamental": {
                        "interestCoverage": 0.0,
                        "grossMarginTTM": 47.8624,
                        "netProfitMarginTTM": 27.1518,
                    },
                }
            ]
        }
    )
    cache = _cache(get_return=None)
    provider = SchwabFundamentalsProvider(client=client, cache=cache)

    with patch("screener.data.schwab.fundamentals_provider.logger") as mock_logger:
        snap = await provider.get_fundamentals("AAPL")

    assert snap.interest_coverage == 0.0
    mock_logger.warning.assert_any_call("interest_coverage_suspicious_zero", symbol="AAPL")


async def test_interest_coverage_nonzero_does_not_log_suspicious_warning():
    client = _client(
        call_return={
            "instruments": [
                {
                    "symbol": "AAPL",
                    "assetType": "EQUITY",
                    "fundamental": {
                        "interestCoverage": 8.2,
                        "grossMarginTTM": 47.8624,
                        "netProfitMarginTTM": 27.1518,
                    },
                }
            ]
        }
    )
    cache = _cache(get_return=None)
    provider = SchwabFundamentalsProvider(client=client, cache=cache)

    with patch("screener.data.schwab.fundamentals_provider.logger") as mock_logger:
        snap = await provider.get_fundamentals("AAPL")

    assert snap.interest_coverage == 8.2
    for call in mock_logger.warning.call_args_list:
        assert call.args[0] != "interest_coverage_suspicious_zero"


async def test_instruments_list_matches_entry_by_symbol_field():
    """The response envelope is a top-level list — if it ever contains more
    than one entry, the entry whose own `symbol` field matches the
    requested symbol must be selected, not blindly `instruments[0]`."""
    client = _client(
        call_return={
            "instruments": [
                {
                    "symbol": "MSFT",
                    "assetType": "EQUITY",
                    "fundamental": {"grossMarginTTM": 68.3092},
                },
                {
                    "symbol": "AAPL",
                    "assetType": "EQUITY",
                    "fundamental": {"grossMarginTTM": 47.8624},
                },
            ]
        }
    )
    cache = _cache(get_return=None)
    provider = SchwabFundamentalsProvider(client=client, cache=cache)

    snap = await provider.get_fundamentals("AAPL")

    assert snap.gross_margin == 47.8624 / 100


async def test_empty_instruments_list_same_all_none_no_exception():
    client = _client(call_return={"instruments": []})
    cache = _cache(get_return=None)
    provider = SchwabFundamentalsProvider(client=client, cache=cache)

    snap = await provider.get_fundamentals("ZZZZ")

    _assert_all_none_zero_years(snap, "ZZZZ")


async def test_missing_instruments_key_in_response_same_all_none_no_exception():
    client = _client(call_return={})  # instruments key missing entirely
    cache = _cache(get_return=None)
    provider = SchwabFundamentalsProvider(client=client, cache=cache)

    snap = await provider.get_fundamentals("ZZZZ")

    _assert_all_none_zero_years(snap, "ZZZZ")


async def test_non_dict_response_same_all_none_no_exception():
    client = _client(call_return=None)  # malformed payload
    cache = _cache(get_return=None)
    provider = SchwabFundamentalsProvider(client=client, cache=cache)

    snap = await provider.get_fundamentals("ZZZZ")

    _assert_all_none_zero_years(snap, "ZZZZ")


async def test_client_call_raises_still_returns_all_none_snapshot():
    client = _client(call_side_effect=RuntimeError("boom"))
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
    client = _client()
    cache = _cache(get_return=cached_snapshot)
    provider = SchwabFundamentalsProvider(client=client, cache=cache)

    snap = await provider.get_fundamentals("AAPL")

    assert snap is cached_snapshot
    client.call.assert_not_called()
    cache.put.assert_not_called()
