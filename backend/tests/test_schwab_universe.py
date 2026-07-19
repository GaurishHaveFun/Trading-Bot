"""Tests for SchwabLosersUniverse (Milestone 3)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from screener.data.schwab.universe import SchwabLosersUniverse


def _movers_payload(symbols: list[str]) -> dict:
    return {"screeners": [{"symbol": s} for s in symbols]}


def _quote_entry(
    change_pct: float,
    market_cap: float = 50_000_000_000,
    quote_type: str = "EQUITY",
    price_to_book: float | None = 5.0,
    industry: str | None = None,
    sector: str | None = None,
) -> dict:
    return {
        "assetMainType": quote_type,
        "quote": {"netPercentChange": change_pct},
        "fundamental": {"pbRatio": price_to_book, "marketCap": market_cap},
        "reference": {"industry": industry, "sector": sector},
    }


def _client(side_effect: list) -> MagicMock:
    client = MagicMock()
    client.get = AsyncMock(side_effect=side_effect)
    return client


# --- cap-floor / quote-type filtering (applied on the flattened quotes
# response, since Schwab's movers payload itself has no market-cap/quote-
# type field — see universe.py's `_filter_and_rank` docstring) ---


async def test_drops_penny_stocks_below_cap_floor():
    movers = _movers_payload(["BIGCO", "PENNY"])
    quotes = {
        "BIGCO": _quote_entry(-5.0, market_cap=50_000_000_000),
        "PENNY": _quote_entry(-20.0, market_cap=100_000_000),  # below $10B floor
    }
    client = _client([movers, quotes])
    u = SchwabLosersUniverse(client=client, watchlist=set())

    symbols = await u.get_symbols()

    assert "BIGCO" in symbols
    assert "PENNY" not in symbols


async def test_drops_non_equity_quote_types():
    movers = _movers_payload(["BIGCO", "SOMEETF"])
    quotes = {
        "BIGCO": _quote_entry(-5.0),
        "SOMEETF": _quote_entry(-10.0, quote_type="ETF"),
    }
    client = _client([movers, quotes])
    u = SchwabLosersUniverse(client=client, watchlist=set())

    symbols = await u.get_symbols()

    assert "BIGCO" in symbols
    assert "SOMEETF" not in symbols


# --- top-15 cap ---


async def test_top_15_cap_enforced():
    symbols_in = [f"SYM{i}" for i in range(20)]
    movers = _movers_payload(symbols_in)
    quotes = {s: _quote_entry(-(i + 1.0)) for i, s in enumerate(symbols_in)}
    client = _client([movers, quotes])
    u = SchwabLosersUniverse(client=client, watchlist=set())

    symbols = await u.get_symbols()

    assert len(symbols) == 15
    expected_top15 = {f"SYM{i}" for i in range(5, 20)}
    assert set(symbols) == expected_top15


async def test_fewer_than_15_qualifying_losers_returns_all():
    symbols_in = [f"SYM{i}" for i in range(5)]
    movers = _movers_payload(symbols_in)
    quotes = {s: _quote_entry(-(i + 1.0)) for i, s in enumerate(symbols_in)}
    client = _client([movers, quotes])
    u = SchwabLosersUniverse(client=client, watchlist=set())

    symbols = await u.get_symbols()

    assert len(symbols) == 5


# --- watchlist union ---


async def test_watchlist_union_adds_down_symbols_not_in_screen():
    movers = _movers_payload(["BIGCO"])
    mover_quotes = {"BIGCO": _quote_entry(-5.0)}
    # _watchlist_down_today iterates sorted(watchlist) -> AAPL, then NVDA
    aapl_quote = {"AAPL": _quote_entry(1.2, price_to_book=30.0, market_cap=3e12)}  # up today
    nvda_quote = {"NVDA": _quote_entry(-3.5, price_to_book=40.0, market_cap=1e12)}  # down today

    client = _client([movers, mover_quotes, aapl_quote, nvda_quote])
    u = SchwabLosersUniverse(client=client, watchlist={"NVDA", "AAPL"})

    symbols = await u.get_symbols()

    assert "BIGCO" in symbols
    assert "NVDA" in symbols  # down today -> unioned in
    assert "AAPL" not in symbols  # up today -> excluded


async def test_watchlist_symbol_already_in_losers_not_duplicated():
    movers = _movers_payload(["NVDA"])
    mover_quotes = {"NVDA": _quote_entry(-8.0)}
    nvda_watchlist_quote = {"NVDA": _quote_entry(-8.0)}

    client = _client([movers, mover_quotes, nvda_watchlist_quote])
    u = SchwabLosersUniverse(client=client, watchlist={"NVDA"})

    symbols = await u.get_symbols()

    assert symbols.count("NVDA") == 1


# --- get_quotes() shape ---


async def test_get_quotes_shape():
    movers = _movers_payload(["BIGCO"])
    quotes = {
        "BIGCO": _quote_entry(-5.0, price_to_book=3.2, market_cap=20_000_000_000)
    }
    client = _client([movers, quotes])
    u = SchwabLosersUniverse(client=client, watchlist=set())

    await u.get_symbols()
    result = await u.get_quotes()

    assert "BIGCO" in result
    meta = result["BIGCO"]
    assert set(meta.keys()) == {"price_to_book", "change_pct", "market_cap", "industry", "sector"}
    assert meta["price_to_book"] == 3.2
    assert meta["change_pct"] == -5.0
    assert meta["market_cap"] == 20_000_000_000
    assert meta["industry"] is None
    assert meta["sector"] is None


async def test_get_quotes_empty_before_get_symbols_called():
    client = MagicMock()
    client.get = AsyncMock()
    u = SchwabLosersUniverse(client=client, watchlist=set())

    result = await u.get_quotes()

    assert result == {}


async def test_get_quotes_includes_watchlist_union_symbols():
    movers = _movers_payload(["BIGCO"])
    mover_quotes = {"BIGCO": _quote_entry(-5.0)}
    nvda_quote = {"NVDA": _quote_entry(-2.0, price_to_book=40.0, market_cap=1e12)}

    client = _client([movers, mover_quotes, nvda_quote])
    u = SchwabLosersUniverse(client=client, watchlist={"NVDA"})

    await u.get_symbols()
    result = await u.get_quotes()

    assert "NVDA" in result
    assert result["NVDA"]["change_pct"] == -2.0


# --- quote_for (single-symbol debug helper) ---


async def test_flatten_quote_entry_reads_net_percent_change_confirmed_field_name():
    """Confirmed against the real Schwab OpenAPI spec: QuoteEquity's field is
    `netPercentChange`, NOT `netPercentChangeInDouble` (a previously-fixed
    bug). This directly exercises `_flatten_quote_entry`'s read of the
    correct key."""
    entry = _quote_entry(-2.75)
    flattened = SchwabLosersUniverse._flatten_quote_entry("BIGCO", entry)

    assert flattened["netPercentChange"] == -2.75


async def test_quote_for_returns_meta_for_arbitrary_symbol():
    client = _client([{"NVDA": _quote_entry(-4.0, price_to_book=12.0, market_cap=5e11)}])
    u = SchwabLosersUniverse(client=client, watchlist=set())

    meta = await u.quote_for("NVDA")

    assert meta == {
        "price_to_book": 12.0,
        "change_pct": -4.0,
        "market_cap": 5e11,
        "industry": None,
        "sector": None,
    }


async def test_quote_for_includes_industry_when_present():
    client = _client([
        {
            "SKYT": _quote_entry(
                -4.0,
                price_to_book=12.0,
                market_cap=5e11,
                industry="Semiconductors",
                sector="Technology",
            )
        }
    ])
    u = SchwabLosersUniverse(client=client, watchlist=set())

    meta = await u.quote_for("SKYT")

    assert meta["industry"] == "Semiconductors"
    assert meta["sector"] == "Technology"


async def test_quote_for_returns_none_on_fetch_error():
    client = MagicMock()
    client.get = AsyncMock(side_effect=RuntimeError("boom"))
    u = SchwabLosersUniverse(client=client, watchlist=set())

    meta = await u.quote_for("BADSYM")

    assert meta is None


# --- resilience ---


async def test_movers_error_returns_empty_symbols_list():
    client = MagicMock()
    client.get = AsyncMock(side_effect=RuntimeError("network down"))
    u = SchwabLosersUniverse(client=client, watchlist=set())

    symbols = await u.get_symbols()

    assert symbols == []


async def test_movers_non_dict_result_treated_as_empty():
    client = _client([None])
    u = SchwabLosersUniverse(client=client, watchlist=set())

    symbols = await u.get_symbols()

    assert symbols == []


async def test_quotes_batch_error_after_movers_success_yields_no_candidates():
    movers = _movers_payload(["BIGCO"])
    client = MagicMock()
    client.get = AsyncMock(side_effect=[movers, RuntimeError("quotes down")])
    u = SchwabLosersUniverse(client=client, watchlist=set())

    symbols = await u.get_symbols()

    assert symbols == []
