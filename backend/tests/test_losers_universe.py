"""Tests for LosersUniverse."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from screener.universe.losers import LosersUniverse


def _quote(
    symbol: str,
    change_pct: float,
    market_cap: float = 50_000_000_000,
    quote_type: str = "EQUITY",
    price_to_book: float | None = 5.0,
) -> dict:
    return {
        "symbol": symbol,
        "regularMarketChangePercent": change_pct,
        "marketCap": market_cap,
        "quoteType": quote_type,
        "priceToBook": price_to_book,
    }


def _screen_result(quotes: list[dict]) -> dict:
    return {"quotes": quotes}


def _make_ticker(info: dict | None):
    mock = MagicMock()
    mock.info = info or {}
    return mock


# --- cap-floor / quote-type filtering ---

async def test_drops_penny_stocks_below_cap_floor():
    quotes = [
        _quote("BIGCO", change_pct=-5.0, market_cap=50_000_000_000),
        _quote("PENNY", change_pct=-20.0, market_cap=100_000_000),  # below $10B floor
    ]
    with patch("screener.universe.losers.yf.screen", return_value=_screen_result(quotes)):
        u = LosersUniverse(watchlist=set())
        symbols = await u.get_symbols()
    assert "BIGCO" in symbols
    assert "PENNY" not in symbols


async def test_drops_non_equity_quote_types():
    quotes = [
        _quote("BIGCO", change_pct=-5.0),
        _quote("SOMEETF", change_pct=-10.0, quote_type="ETF"),
    ]
    with patch("screener.universe.losers.yf.screen", return_value=_screen_result(quotes)):
        u = LosersUniverse(watchlist=set())
        symbols = await u.get_symbols()
    assert "BIGCO" in symbols
    assert "SOMEETF" not in symbols


# --- top-20 cap ---

async def test_top_20_cap_enforced():
    # 25 qualifying losers, ranked by % loss (most negative first)
    quotes = [_quote(f"SYM{i}", change_pct=-(i + 1.0)) for i in range(25)]
    with patch("screener.universe.losers.yf.screen", return_value=_screen_result(quotes)):
        u = LosersUniverse(watchlist=set())
        symbols = await u.get_symbols()
    assert len(symbols) == 20
    # The most negative (largest loss) symbols should be SYM24..SYM5 (top 20 by loss)
    expected_top20 = {f"SYM{i}" for i in range(5, 25)}
    assert set(symbols) == expected_top20


async def test_fewer_than_20_qualifying_losers_returns_all():
    quotes = [_quote(f"SYM{i}", change_pct=-(i + 1.0)) for i in range(5)]
    with patch("screener.universe.losers.yf.screen", return_value=_screen_result(quotes)):
        u = LosersUniverse(watchlist=set())
        symbols = await u.get_symbols()
    assert len(symbols) == 5


# --- watchlist union ---

async def test_watchlist_union_adds_down_symbols_not_in_screen():
    quotes = [_quote("BIGCO", change_pct=-5.0)]
    watchlist_info = {
        "NVDA": {"regularMarketChangePercent": -3.5, "priceToBook": 40.0, "marketCap": 1e12},
        "AAPL": {"regularMarketChangePercent": 1.2, "priceToBook": 30.0, "marketCap": 3e12},  # up today
    }

    def fake_ticker(symbol):
        return _make_ticker(watchlist_info.get(symbol))

    with patch("screener.universe.losers.yf.screen", return_value=_screen_result(quotes)), \
         patch("screener.universe.losers.yf.Ticker", side_effect=fake_ticker):
        u = LosersUniverse(watchlist={"NVDA", "AAPL"})
        symbols = await u.get_symbols()

    assert "BIGCO" in symbols
    assert "NVDA" in symbols  # down today -> unioned in
    assert "AAPL" not in symbols  # up today -> excluded


async def test_watchlist_symbol_already_in_losers_not_duplicated():
    quotes = [_quote("NVDA", change_pct=-8.0)]

    def fake_ticker(symbol):
        return _make_ticker({"regularMarketChangePercent": -8.0, "priceToBook": 40.0, "marketCap": 1e12})

    with patch("screener.universe.losers.yf.screen", return_value=_screen_result(quotes)), \
         patch("screener.universe.losers.yf.Ticker", side_effect=fake_ticker):
        u = LosersUniverse(watchlist={"NVDA"})
        symbols = await u.get_symbols()

    assert symbols.count("NVDA") == 1


# --- get_quotes() shape ---

async def test_get_quotes_shape():
    quotes = [_quote("BIGCO", change_pct=-5.0, price_to_book=3.2, market_cap=20_000_000_000)]
    with patch("screener.universe.losers.yf.screen", return_value=_screen_result(quotes)):
        u = LosersUniverse(watchlist=set())
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
    u = LosersUniverse(watchlist=set())
    assert await u.get_quotes() == {}


async def test_get_quotes_includes_watchlist_union_symbols():
    quotes = [_quote("BIGCO", change_pct=-5.0)]

    def fake_ticker(symbol):
        return _make_ticker({"regularMarketChangePercent": -2.0, "priceToBook": 40.0, "marketCap": 1e12})

    with patch("screener.universe.losers.yf.screen", return_value=_screen_result(quotes)), \
         patch("screener.universe.losers.yf.Ticker", side_effect=fake_ticker):
        u = LosersUniverse(watchlist={"NVDA"})
        await u.get_symbols()
        result = await u.get_quotes()

    assert "NVDA" in result
    assert result["NVDA"]["change_pct"] == -2.0


# --- quote_for (single-symbol debug helper) ---

async def test_quote_for_returns_meta_for_arbitrary_symbol():
    with patch("screener.universe.losers.yf.Ticker", return_value=_make_ticker(
        {"regularMarketChangePercent": -4.0, "priceToBook": 12.0, "marketCap": 5e11}
    )):
        u = LosersUniverse(watchlist=set())
        meta = await u.quote_for("NVDA")
    assert meta == {
        "price_to_book": 12.0,
        "change_pct": -4.0,
        "market_cap": 5e11,
        "industry": None,
        "sector": None,
    }


async def test_quote_for_includes_industry_when_present():
    with patch("screener.universe.losers.yf.Ticker", return_value=_make_ticker(
        {
            "regularMarketChangePercent": -4.0,
            "priceToBook": 12.0,
            "marketCap": 5e11,
            "industry": "Semiconductors",
            "sector": "Technology",
        }
    )):
        u = LosersUniverse(watchlist=set())
        meta = await u.quote_for("SKYT")
    assert meta["industry"] == "Semiconductors"
    assert meta["sector"] == "Technology"


async def test_quote_for_returns_none_on_fetch_error():
    with patch("screener.universe.losers.yf.Ticker", side_effect=RuntimeError("boom")):
        u = LosersUniverse(watchlist=set())
        meta = await u.quote_for("BADSYM")
    assert meta is None


# --- resilience ---

async def test_screen_error_returns_empty_losers_list():
    with patch("screener.universe.losers.yf.screen", side_effect=RuntimeError("network down")):
        u = LosersUniverse(watchlist=set())
        symbols = await u.get_symbols()
    assert symbols == []


async def test_screen_non_dict_result_treated_as_empty():
    with patch("screener.universe.losers.yf.screen", return_value=None):
        u = LosersUniverse(watchlist=set())
        symbols = await u.get_symbols()
    assert symbols == []


@pytest.mark.integration
async def test_real_day_losers_fetch():
    """Live network test — skipped in CI with -m 'not integration'."""
    u = LosersUniverse(watchlist={"NVDA", "AAPL"})
    symbols = await u.get_symbols()
    assert isinstance(symbols, list)
    quotes = await u.get_quotes()
    for symbol in symbols:
        assert symbol in quotes
        assert "price_to_book" in quotes[symbol]
        assert "change_pct" in quotes[symbol]
        assert "market_cap" in quotes[symbol]
