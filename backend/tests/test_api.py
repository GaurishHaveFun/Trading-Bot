"""Tests for the read-only FastAPI HTTP service (`screener.api.app`).
Everything here mocks the underlying providers/universes — no real network
calls — so this file runs under `pytest -m "not integration"`."""
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from screener.api.app import app, merge_losers_quotes

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
        "AAA": {"change_pct": -5.0, "market_cap": 1e11},
        "BBB": {"change_pct": -2.0, "market_cap": 2e11},  # also in schwab -> schwab wins
        "CCC": {"change_pct": -8.0, "market_cap": 3e11},
    }
    schwab_quotes = {
        "BBB": {"change_pct": -9.0, "market_cap": 2.5e11},  # overrides yfinance's BBB
        "DDD": {"change_pct": -1.0, "market_cap": 4e11},
    }

    ranked = merge_losers_quotes(yfinance_quotes, schwab_quotes, limit=3)

    assert [t for t, _ in ranked] == ["BBB", "CCC", "AAA"]
    assert ranked[0][1]["change_pct"] == -9.0  # schwab's value won for BBB


def test_merge_losers_quotes_respects_limit():
    quotes = {f"SYM{i}": {"change_pct": -float(i), "market_cap": 1e10} for i in range(10)}
    ranked = merge_losers_quotes(quotes, {}, limit=3)
    assert len(ranked) == 3
    assert [t for t, _ in ranked] == ["SYM9", "SYM8", "SYM7"]


# --- /losers ---


def test_losers_merges_dedupes_and_respects_limit():
    yfinance_universe = _FakeUniverse(
        quotes={
            "AAA": {"change_pct": -5.0, "market_cap": 1e11},
            "BBB": {"change_pct": -2.0, "market_cap": 2e11},
        }
    )
    schwab_universe = _FakeUniverse(
        quotes={
            "BBB": {"change_pct": -9.0, "market_cap": 2.5e11},
            "CCC": {"change_pct": -1.0, "market_cap": 3e11},
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


def test_losers_falls_back_to_yfinance_only_when_schwab_raises():
    yfinance_universe = _FakeUniverse(
        quotes={"AAA": {"change_pct": -5.0, "market_cap": 1e11}}
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
