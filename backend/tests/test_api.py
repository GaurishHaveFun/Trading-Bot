"""Tests for the read-only FastAPI HTTP service (`screener.api.app`).
Everything here mocks the underlying providers/universes — no real network
calls — so this file runs under `pytest -m "not integration"`."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from screener.api.app import (
    _looks_like_real_symbol,
    _num,
    _pick,
    app,
    merge_losers_quotes,
)
from screener.models import Bar

client = TestClient(app)


class _FakeUniverse:
    """Stand-in for `LosersUniverse`/`SchwabLosersUniverse`: same
    `get_symbols()`/`get_quotes()` async shape, no network I/O."""

    def __init__(self, quotes: dict[str, dict] | None = None, error: Exception | None = None):
        self._quotes = quotes or {}
        self._error = error

    async def get_symbols(self, *args, **kwargs):
        if self._error is not None:
            raise self._error
        return list(self._quotes.keys())

    async def get_quotes(self, *args, **kwargs):
        return self._quotes


def _patch_universes(yfinance_universe: _FakeUniverse, schwab_universe: _FakeUniverse):
    return (
        patch("screener.api.app.LosersUniverse", return_value=yfinance_universe),
        patch("screener.api.app.SchwabLosersUniverse", return_value=schwab_universe),
        patch("screener.api.app.load_watchlist", return_value=set()),
    )


# --- /health ---


def test_health_returns_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# --- /quotes ---


def test_quotes_returns_expected_shape_and_skips_failing_symbol():
    def fake_fetch_info(symbol: str):
        if symbol == "AAPL":
            return {
                "regularMarketPrice": 192.31,
                "regularMarketChangePercent": -1.85,
                "regularMarketPreviousClose": 195.9,
                "currency": "USD",
            }
        if symbol == "BADSYM":
            raise RuntimeError("no data")
        return {}

    with patch("screener.api.app._fetch_quote_info", side_effect=fake_fetch_info):
        resp = client.get("/quotes", params={"symbols": "AAPL,BADSYM"})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0] == {
        "ticker": "AAPL",
        "price": 192.31,
        "change_pct": -1.85,
        "previous_close": 195.9,
        "currency": "USD",
    }


def test_quotes_skips_symbol_with_empty_info():
    def fake_fetch_info(symbol: str):
        if symbol == "MSFT":
            return {"regularMarketPrice": 410.0, "currency": "USD"}
        return {}  # falsy -> treated as fetch failure

    with patch("screener.api.app._fetch_quote_info", side_effect=fake_fetch_info):
        resp = client.get("/quotes", params={"symbols": "MSFT,EMPTY"})

    body = resp.json()
    assert [q["ticker"] for q in body] == ["MSFT"]


# --- merge_losers_quotes (pure helper) ---


def test_merge_losers_quotes_dedupes_schwab_wins_and_ranks_and_caps():
    yfinance_quotes = {
        "AAA": {"change_pct": -5.0, "market_cap": 1e11, "sector": "Technology"},
        "BBB": {"change_pct": -2.0, "market_cap": 2e11, "sector": "Healthcare"},  # also in schwab -> schwab wins
        "CCC": {"change_pct": -8.0, "market_cap": 3e11, "sector": "Financials"},
    }
    schwab_quotes = {
        "BBB": {"change_pct": -9.0, "market_cap": 2.5e11, "sector": "Health Care"},  # overrides yfinance's BBB
        "DDD": {"change_pct": -1.0, "market_cap": 4e11, "sector": "Energy"},
    }

    ranked = merge_losers_quotes(yfinance_quotes, schwab_quotes, limit=3)

    assert [t for t, _ in ranked] == ["BBB", "CCC", "AAA"]
    assert ranked[0][1]["change_pct"] == -9.0  # schwab's value won for BBB
    assert ranked[0][1]["sector"] == "Health Care"  # schwab's value won for BBB


def test_merge_losers_quotes_respects_limit():
    quotes = {f"SYM{i}": {"change_pct": -float(i), "market_cap": 1e10} for i in range(10)}
    ranked = merge_losers_quotes(quotes, {}, limit=3)
    assert len(ranked) == 3
    assert [t for t, _ in ranked] == ["SYM9", "SYM8", "SYM7"]


# --- /losers ---


def test_losers_merges_dedupes_and_respects_limit():
    yfinance_universe = _FakeUniverse(
        quotes={
            "AAA": {"change_pct": -5.0, "market_cap": 1e11, "sector": "Technology"},
            "BBB": {"change_pct": -2.0, "market_cap": 2e11, "sector": "Healthcare"},
        }
    )
    schwab_universe = _FakeUniverse(
        quotes={
            "BBB": {"change_pct": -9.0, "market_cap": 2.5e11, "sector": "Health Care"},
            "CCC": {"change_pct": -1.0, "market_cap": 3e11, "sector": "Financials"},
        }
    )

    patchers = _patch_universes(yfinance_universe, schwab_universe)
    with patchers[0], patchers[1], patchers[2], \
         patch("screener.api.app._fetch_quote_info", return_value={"regularMarketPrice": 42.0}):
        resp = client.get("/losers", params={"limit": 2})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["ticker"] == "BBB"
    assert body[0]["change_pct"] == -9.0  # schwab-sourced value won
    assert body[0]["market_cap"] == 2.5e11
    assert body[0]["price"] == 42.0
    assert body[0]["sector"] == "Health Care"  # schwab-sourced value won


def test_losers_falls_back_to_info_sector_when_meta_sector_is_none():
    # Schwab wins the merge for BBB but has no sector data (sector: None) --
    # the response should fall back to the sector from the freshly-fetched
    # yfinance `info` dict rather than surfacing None.
    yfinance_universe = _FakeUniverse(
        quotes={
            "BBB": {"change_pct": -2.0, "market_cap": 2e11, "sector": "Healthcare"},
        }
    )
    schwab_universe = _FakeUniverse(
        quotes={
            "BBB": {"change_pct": -9.0, "market_cap": 2.5e11, "sector": None},
        }
    )

    def fake_fetch_info(symbol: str):
        return {"regularMarketPrice": 42.0, "sector": "Technology"}

    patchers = _patch_universes(yfinance_universe, schwab_universe)
    with patchers[0], patchers[1], patchers[2], \
         patch("screener.api.app._fetch_quote_info", side_effect=fake_fetch_info):
        resp = client.get("/losers", params={"limit": 2})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["ticker"] == "BBB"
    assert body[0]["change_pct"] == -9.0  # schwab-sourced value still wins
    assert body[0]["market_cap"] == 2.5e11  # schwab-sourced value still wins
    assert body[0]["price"] == 42.0
    assert body[0]["sector"] == "Technology"  # fell back to info's sector


def test_losers_falls_back_to_yfinance_only_when_schwab_raises():
    yfinance_universe = _FakeUniverse(
        quotes={"AAA": {"change_pct": -5.0, "market_cap": 1e11, "sector": "Technology"}}
    )
    schwab_universe = _FakeUniverse(error=RuntimeError("no schwab token"))

    patchers = _patch_universes(yfinance_universe, schwab_universe)
    with patchers[0], patchers[1], patchers[2], \
         patch("screener.api.app._fetch_quote_info", return_value={"regularMarketPrice": 10.0}):
        resp = client.get("/losers", params={"limit": 20})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["ticker"] == "AAA"
    assert body[0]["sector"] == "Technology"


# --- pure helpers: _pick / _num / _looks_like_real_symbol ---


def test_pick_returns_first_non_none_value_not_first_truthy():
    # Regression guard for the `a or b` bug: 0.0 is a legitimate value and
    # must not be skipped in favor of a later key.
    assert _pick({"a": 0.0, "b": 5.0}, "a", "b") == 0.0


def test_pick_skips_none_and_missing_keys():
    assert _pick({"a": None, "b": None, "c": 3.0}, "a", "b", "c") == 3.0
    assert _pick({}, "a", "b") is None


def test_num_maps_nan_inf_and_non_numeric_to_none():
    assert _num(float("nan")) is None
    assert _num(float("inf")) is None
    assert _num(float("-inf")) is None
    assert _num(None) is None
    assert _num("n/a") is None
    assert _num(3.5) == 3.5
    assert _num(0) == 0.0


def test_looks_like_real_symbol_rejects_garbage_info():
    assert _looks_like_real_symbol({"trailingPegRatio": None}) is False
    assert _looks_like_real_symbol({}) is False
    assert _looks_like_real_symbol({"regularMarketPrice": 100.0}) is True
    assert _looks_like_real_symbol({"longName": "Apple Inc."}) is True


# --- /tickers/{symbol} ---


_FULL_INFO = {
    "regularMarketPrice": 192.31,
    "regularMarketChangePercent": -1.85,
    "regularMarketPreviousClose": 195.9,
    "regularMarketOpen": 194.0,
    "regularMarketDayHigh": 196.0,
    "regularMarketDayLow": 191.0,
    "marketCap": 3.0e12,
    "trailingPE": 30.1,
    "forwardPE": 28.4,
    "priceToBook": 45.2,
    "trailingEps": 6.4,
    "forwardEps": 6.8,
    "dividendYield": 0.5,
    "beta": 1.2,
    "fiftyTwoWeekHigh": 250.0,
    "fiftyTwoWeekLow": 150.0,
    "regularMarketVolume": 54_000_000,
    "averageVolume": 60_000_000,
    "averageVolume10days": 58_000_000,
    "longName": "Apple Inc.",
    "shortName": "Apple",
    "longBusinessSummary": "Apple designs consumer electronics.",
    "sector": "Technology",
    "industry": "Consumer Electronics",
    "fullTimeEmployees": 164000,
    "website": "https://www.apple.com",
    "country": "United States",
    "fullExchangeName": "NASDAQ",
    "currency": "USD",
    "targetMeanPrice": 210.0,
    "targetHighPrice": 250.0,
    "targetLowPrice": 180.0,
    "targetMedianPrice": 205.0,
    "recommendationKey": "buy",
    "numberOfAnalystOpinions": 35,
}


def _make_bars(n: int) -> list[Bar]:
    return [
        Bar(
            timestamp=datetime(2026, 1, i + 1, tzinfo=timezone.utc),
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=1_000_000 + i,
        )
        for i in range(n)
    ]


class _FakeBarProvider:
    def __init__(self, bars: list[Bar] | None = None, error: Exception | None = None):
        self._bars = bars or []
        self._error = error

    async def get_bars(self, symbol, start, end, interval="1d"):
        if self._error is not None:
            raise self._error
        return self._bars


def test_ticker_detail_full_shape():
    with patch("screener.api.app._fetch_quote_info", return_value=_FULL_INFO), \
         patch("screener.api.app._get_bar_provider", return_value=_FakeBarProvider(_make_bars(2))):
        resp = client.get("/tickers/AAPL")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ticker"] == "AAPL"
    assert body["profile"]["long_name"] == "Apple Inc."
    assert body["profile"]["employees"] == 164000
    assert body["stats"]["price"] == 192.31
    assert body["stats"]["market_cap"] == 3.0e12
    assert body["targets"]["mean"] == 210.0
    assert body["targets"]["recommendation_key"] == "buy"
    assert len(body["bars"]) == 2
    assert body["bars"][0]["ticker"] == "AAPL"


def test_ticker_detail_lowercases_symbol_to_uppercase_ticker():
    with patch("screener.api.app._fetch_quote_info", return_value=_FULL_INFO), \
         patch("screener.api.app._get_bar_provider", return_value=_FakeBarProvider([])):
        resp = client.get("/tickers/aapl")

    assert resp.status_code == 200
    assert resp.json()["ticker"] == "AAPL"


def test_ticker_detail_404_for_empty_info():
    with patch("screener.api.app._fetch_quote_info", return_value={}), \
         patch("screener.api.app._get_bar_provider", return_value=_FakeBarProvider([])):
        resp = client.get("/tickers/ZZZZZZZZ")

    assert resp.status_code == 404


def test_ticker_detail_404_not_500_when_quote_fetch_raises():
    with patch("screener.api.app._fetch_quote_info", side_effect=RuntimeError("boom")), \
         patch("screener.api.app._get_bar_provider", return_value=_FakeBarProvider([])):
        resp = client.get("/tickers/AAPL")

    assert resp.status_code == 404


def test_ticker_detail_returns_empty_bars_when_bar_provider_raises():
    with patch("screener.api.app._fetch_quote_info", return_value=_FULL_INFO), \
         patch("screener.api.app._get_bar_provider", return_value=_FakeBarProvider(error=RuntimeError("no bars"))):
        resp = client.get("/tickers/AAPL")

    assert resp.status_code == 200
    assert resp.json()["bars"] == []


def test_ticker_detail_nan_stat_becomes_null_in_json():
    info = dict(_FULL_INFO)
    info["trailingPE"] = float("nan")
    with patch("screener.api.app._fetch_quote_info", return_value=info), \
         patch("screener.api.app._get_bar_provider", return_value=_FakeBarProvider([])):
        resp = client.get("/tickers/AAPL")

    assert resp.status_code == 200
    assert resp.json()["stats"]["trailing_pe"] is None
