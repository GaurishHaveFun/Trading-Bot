"""Yahoo snapshot store, push endpoint, /tickers fallthrough, /losers sector
and the pusher helpers. No network."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from screener.api import snapshot_pusher
from screener.api.app import app, merge_ticker_info
from screener.api.schwab_quotes import shape_instrument_entry
from screener.api.yahoo_snapshot import YahooSnapshotStore, sanitize_info
from screener.config import get_settings

client = TestClient(app)

SNAP = {
    "longName": "Apple Inc.",
    "sector": "Technology",
    "regularMarketPrice": 100.0,
    "regularMarketDayHigh": 101.0,
    "targetMeanPrice": 210.0,
}


@pytest.fixture(autouse=True)
def _settings(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "yahoo_snapshot_db_path", str(tmp_path / "snap.db"))
    monkeypatch.setattr(s, "push_secret", "s3cret")


@pytest.fixture(autouse=True)
def _no_live():
    with patch("screener.api.app._fetch_quote_info", side_effect=RuntimeError("blocked")), \
         patch("screener.api.app._fetch_schwab_quotes_raw", side_effect=RuntimeError("no schwab")), \
         patch("screener.api.app._fetch_schwab_fundamentals_raw", side_effect=RuntimeError("no schwab")), \
         patch("screener.api.app._get_bar_provider") as bp:
        bp.return_value.get_bars = AsyncMock(return_value=[])
        yield


def push(symbols, secret="s3cret"):
    headers = {} if secret is None else {"X-Push-Secret": secret}
    return client.post("/internal/yahoo-snapshot", json={"snapshots": symbols}, headers=headers)


# --- store ---


def test_sanitize_drops_nan_inf_unknown_and_containers():
    out = sanitize_info(
        {"trailingPE": float("nan"), "beta": float("inf"), "marketCap": 5, "junk": 1,
         "sector": "Tech", "website": None, "longName": ["x"], "fullTimeEmployees": 10}
    )
    assert out == {"marketCap": 5, "sector": "Tech", "fullTimeEmployees": 10}


def test_store_roundtrip(tmp_path):
    store = YahooSnapshotStore(tmp_path / "s.db")
    assert asyncio.run(store.get("AAPL")) is None
    n = asyncio.run(store.upsert_many({"aapl": {**SNAP, "beta": float("nan")}, "EMPTY": {"junk": 1}}))
    assert n == 1
    info, fetched_at = asyncio.run(store.get("AAPL"))
    assert info["sector"] == "Technology" and "beta" not in info
    assert fetched_at.tzinfo is not None


# --- push endpoint ---


def test_push_missing_and_wrong_secret():
    assert push({"AAPL": SNAP}, secret=None).status_code == 401
    assert push({"AAPL": SNAP}, secret="nope").status_code == 401


def test_push_disabled_when_secret_unset(monkeypatch):
    monkeypatch.setattr(get_settings(), "push_secret", "")
    assert push({"AAPL": SNAP}, secret="").status_code == 503
    assert push({"AAPL": SNAP}, secret=None).status_code == 503


def test_push_success_and_oversize():
    assert push({"AAPL": SNAP}).json() == {"stored": 1}
    big = {f"S{i}": SNAP for i in range(201)}
    assert push(big).status_code == 413


# --- /tickers fallthrough ---


def test_ticker_404_when_all_sources_fail():
    assert client.get("/tickers/AAPL").status_code == 404


def test_ticker_served_from_snapshot_when_schwab_raises():
    push({"AAPL": SNAP})
    resp = client.get("/tickers/AAPL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["profile"]["sector"] == "Technology"
    assert body["stats"]["price"] == 100.0
    assert body["data_sources"] == ["snapshot"]
    assert body["snapshot_fetched_at"] is not None


def test_ticker_merges_schwab_price_over_snapshot():
    push({"AAPL": SNAP})
    quote = {"AAPL": {"regularMarketPrice": 105.0, "regularMarketChangePercent": -1.0,
                      "regularMarketPreviousClose": 106.0, "currency": "USD"}}
    fund = {"marketCap": 3e12, "targetMeanPrice": 1.0}
    with patch("screener.api.app._fetch_schwab_quotes_raw", AsyncMock(return_value=quote)), \
         patch("screener.api.app._fetch_schwab_fundamentals_raw", AsyncMock(return_value=fund)):
        body = client.get("/tickers/AAPL").json()
    assert body["stats"]["price"] == 105.0
    assert body["stats"]["change_pct"] == -1.0
    assert body["stats"]["day_high"] is None  # stale intraday dropped
    assert body["stats"]["market_cap"] == 3e12
    assert body["targets"]["mean"] == 210.0  # snapshot beats Schwab fundamentals
    assert body["data_sources"] == ["snapshot", "schwab"]


def test_ticker_schwab_only_serves_without_snapshot():
    quote = {"AAPL": {"regularMarketPrice": 105.0}}
    fund = {"shortName": "APPLE INC", "marketCap": 3e12}
    with patch("screener.api.app._fetch_schwab_quotes_raw", AsyncMock(return_value=quote)), \
         patch("screener.api.app._fetch_schwab_fundamentals_raw", AsyncMock(return_value=fund)):
        resp = client.get("/tickers/AAPL")
    assert resp.status_code == 200
    assert resp.json()["profile"]["short_name"] == "APPLE INC"


def test_ticker_live_yfinance_wins():
    push({"AAPL": SNAP})
    with patch("screener.api.app._fetch_quote_info", return_value={"regularMarketPrice": 111.0, "longName": "Live"}):
        body = client.get("/tickers/AAPL").json()
    assert body["stats"]["price"] == 111.0
    assert body["profile"]["sector"] == "Technology"
    assert body["snapshot_fetched_at"] is None


def test_merge_ticker_info_priority():
    out = merge_ticker_info({"a": 1}, {"a": 2, "b": 2}, {"b": 3, "c": 3}, {"c": 4, "d": 4})
    assert out == {"a": 1, "b": 3, "c": 3, "d": 4}


def test_shape_instrument_entry():
    shaped = shape_instrument_entry(
        {"description": "APPLE INC", "fundamental": {"marketCap": 3e12, "peRatio": 30.0, "eps": 6.4,
         "high52": 250.0, "low52": 150.0, "dividendYield": 0.5, "beta": 1.2, "vol3MonthAvg": 0}}
    )
    assert shaped["trailingPE"] == 30.0 and shaped["fiftyTwoWeekLow"] == 150.0
    assert "averageVolume" not in shaped and shaped["longName"] == "APPLE INC"
    assert shape_instrument_entry({}) is None


# --- /losers sector ---


class _U:
    def __init__(self, quotes):
        self._q = quotes

    async def get_symbols(self):
        return list(self._q)

    async def get_quotes(self):
        return self._q


def test_losers_sector_from_snapshot_without_yfinance():
    push({"ZZZ": {"sector": "Energy", "longName": "Z"}})
    meta = {"ZZZ": {"change_pct": -3.0, "market_cap": 1e11, "sector": None}}
    with patch("screener.api.app.LosersUniverse", return_value=_U({})), \
         patch("screener.api.app.SchwabLosersUniverse", return_value=_U(meta)), \
         patch("screener.api.app.load_watchlist", return_value=set()), \
         patch("screener.api.app._fetch_schwab_quotes_raw", AsyncMock(return_value={"ZZZ": {"regularMarketPrice": 5.0}})), \
         patch("screener.api.app._fetch_quote_info", side_effect=AssertionError("yfinance used")):
        row = client.get("/losers").json()[0]
    assert row["sector"] == "Energy" and row["price"] == 5.0


# --- pusher helpers ---


def test_chunked_and_rank_losers():
    assert snapshot_pusher.chunked([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
    q = {"A": {"change_pct": -1}, "B": {"change_pct": -5}, "C": {"change_pct": 2}}
    assert snapshot_pusher.rank_losers(q, 2) == ["B", "A"]


def test_fetch_infos_skips_failures():
    def fake(sym):
        if sym == "BAD":
            raise RuntimeError("x")
        return {"regularMarketPrice": 1.0} if sym == "OK" else {}

    with patch("screener.api.snapshot_pusher.fetch_quote_info", side_effect=fake):
        out = asyncio.run(snapshot_pusher.fetch_infos(["OK", "BAD", "EMPTY"]))
    assert list(out) == ["OK"]


def test_push_infos_chunks_with_header():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        body = json.loads(request.content)
        seen.append((request.headers["x-push-secret"], len(body["snapshots"])))
        return httpx.Response(200, json={"stored": len(body["snapshots"])})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await snapshot_pusher.push_infos(
                {f"S{i}": {"beta": 1} for i in range(5)}, "http://x/", "k", chunk_size=2, client=c
            )

    assert asyncio.run(go()) == 5
    assert seen == [("k", 2), ("k", 2), ("k", 1)]
