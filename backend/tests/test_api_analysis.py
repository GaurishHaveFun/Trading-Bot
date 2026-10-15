"""Tests for the `/tickers/{symbol}/analysis` GET/POST routes in
`screener.api.app`. Everything here mocks `analysis_cache`/`analysis_context`/
`analysis_engine` -- no real network/DB calls -- so this file runs under
`pytest -m "not integration"`."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport

from screener.analysis.models import AnalysisEvidence, CachedAnalysis, TickerAnalysis
from screener.api.app import app

client = TestClient(app)


def _sample_analysis(headline: str = "Test headline") -> TickerAnalysis:
    return TickerAnalysis(
        headline=headline,
        confidence="medium",
        drivers=[],
        sector_context=None,
        what_to_watch=[],
        sources=[],
    )


def _cached(ticker="AAPL", generated_at=None, model="claude-opus-5", change_pct=-2.5):
    return CachedAnalysis(
        ticker=ticker,
        generated_at=generated_at or datetime.now(timezone.utc),
        model=model,
        change_pct=change_pct,
        analysis=_sample_analysis(),
    )


def _evidence(ticker="AAPL"):
    return AnalysisEvidence(
        ticker=ticker,
        as_of=datetime.now(timezone.utc),
        profile={},
        stats={},
        targets={},
        technicals={"change_pct_1d": -3.1},
        peers=[],
        news=[],
    )


# --- GET ---


def test_get_analysis_fresh_cache_hit_returns_ready_with_sane_age():
    generated_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    entry = _cached(generated_at=generated_at)

    with patch("screener.api.app.analysis_cache.get_fresh", new=AsyncMock(return_value=entry)):
        resp = client.get("/tickers/AAPL/analysis")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["model"] == "claude-opus-5"
    assert body["analysis"]["headline"] == "Test headline"
    assert 9 <= body["age_minutes"] <= 11


def test_get_analysis_miss_returns_none_status_with_null_fields():
    with patch("screener.api.app.analysis_cache.get_fresh", new=AsyncMock(return_value=None)):
        resp = client.get("/tickers/AAPL/analysis")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "status": "none",
        "generated_at": None,
        "age_minutes": None,
        "model": None,
        "analysis": None,
    }


def test_get_analysis_honors_explicit_max_age_minutes():
    mock_get_fresh = AsyncMock(return_value=None)
    with patch("screener.api.app.analysis_cache.get_fresh", new=mock_get_fresh):
        resp = client.get("/tickers/AAPL/analysis", params={"max_age_minutes": 45})

    assert resp.status_code == 200
    mock_get_fresh.assert_awaited_once_with("AAPL", 45)


@pytest.mark.parametrize("bad_value", [0, 999999])
def test_get_analysis_rejects_out_of_range_max_age_minutes(bad_value):
    resp = client.get("/tickers/AAPL/analysis", params={"max_age_minutes": bad_value})
    assert resp.status_code == 422


# --- POST happy path / not found ---


def test_post_analysis_happy_path_returns_ready_and_writes_cache():
    mock_put = AsyncMock(return_value=None)
    with (
        patch("screener.api.app.analysis_cache.get_fresh", new=AsyncMock(return_value=None)),
        patch("screener.api.app.analysis_context.build_evidence", new=AsyncMock(return_value=_evidence())),
        patch("screener.api.app.analysis_engine.analyze", new=AsyncMock(return_value=_sample_analysis("Fresh"))),
        patch("screener.api.app.analysis_cache.put", new=mock_put),
    ):
        resp = client.post("/tickers/AAPL/analysis")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["analysis"]["headline"] == "Fresh"
    mock_put.assert_awaited_once()


def test_post_analysis_unknown_symbol_returns_404():
    with (
        patch("screener.api.app.analysis_cache.get_fresh", new=AsyncMock(return_value=None)),
        patch("screener.api.app.analysis_context.build_evidence", new=AsyncMock(return_value=None)),
    ):
        resp = client.post("/tickers/NOPE/analysis")

    assert resp.status_code == 404


# --- POST error mapping ---


@pytest.mark.parametrize(
    "exc_attr, expected_status",
    [
        ("AnalysisUnavailable", 503),
        ("AnalysisTimeout", 504),
        ("AnalysisRateLimited", 429),
        ("AnalysisProviderError", 502),
        ("AnalysisRefused", 502),
        ("AnalysisParseError", 502),
    ],
)
def test_post_analysis_maps_engine_errors_to_status_codes(exc_attr, expected_status):
    import screener.analysis.engine as analysis_engine_module

    exc_class = getattr(analysis_engine_module, exc_attr)
    if exc_attr == "AnalysisRefused":
        exc = exc_class("refused")
    else:
        exc = exc_class("boom")

    with (
        patch("screener.api.app.analysis_cache.get_fresh", new=AsyncMock(return_value=None)),
        patch("screener.api.app.analysis_context.build_evidence", new=AsyncMock(return_value=_evidence())),
        patch("screener.api.app.analysis_engine.analyze", new=AsyncMock(side_effect=exc)),
    ):
        resp = client.post("/tickers/AAPL/analysis")

    assert resp.status_code == expected_status


# --- concurrency: two simultaneous POSTs for the same ticker only analyze once ---


async def test_post_analysis_concurrent_requests_only_analyze_once():
    call_count = 0
    put_called = asyncio.Event()

    async def fake_get_fresh(ticker, max_age_minutes):
        # Before the winner has written the cache, every re-check misses.
        # After the winner's put() lands, the loser's re-check (inside the
        # per-ticker lock) sees a fresh hit and skips analyze().
        if put_called.is_set():
            return _cached(ticker=ticker)
        return None

    async def fake_build_evidence(ticker):
        await asyncio.sleep(0.05)
        return _evidence(ticker)

    async def fake_analyze(evidence):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return _sample_analysis("Concurrent")

    async def fake_put(cached):
        put_called.set()

    with (
        patch("screener.api.app.analysis_cache.get_fresh", new=fake_get_fresh),
        patch("screener.api.app.analysis_context.build_evidence", new=fake_build_evidence),
        patch("screener.api.app.analysis_engine.analyze", new=fake_analyze),
        patch("screener.api.app.analysis_cache.put", new=fake_put),
    ):
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp1, resp2 = await asyncio.gather(
                ac.post("/tickers/CONC/analysis"),
                ac.post("/tickers/CONC/analysis"),
            )

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert call_count == 1
