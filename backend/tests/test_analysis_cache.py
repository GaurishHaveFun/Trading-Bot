"""Unit tests for `screener.analysis.cache`. No network access / no real
Postgres -- only the in-process in-memory fallback path (`database_url ==
""`) is exercised, per the task's required coverage."""
from datetime import datetime, timedelta, timezone

import screener.analysis.cache as cache_module
from screener.analysis.models import CachedAnalysis, TickerAnalysis
from screener.config import Settings


def _fake_settings():
    return Settings(database_url="")


def _sample_analysis():
    return TickerAnalysis(
        headline="Test headline",
        confidence="low",
        drivers=[],
        sector_context=None,
        what_to_watch=[],
        sources=[],
    )


def _cached(ticker="TEST", generated_at=None, model="claude-opus-5", change_pct=-5.0):
    return CachedAnalysis(
        ticker=ticker,
        generated_at=generated_at or datetime.now(timezone.utc),
        model=model,
        change_pct=change_pct,
        analysis=_sample_analysis(),
    )


def _reset_memory_store():
    cache_module._memory_store.clear()


# --- round trip ---

async def test_put_then_get_fresh_round_trip(monkeypatch):
    _reset_memory_store()
    monkeypatch.setattr(cache_module, "get_settings", _fake_settings)

    entry = _cached(ticker="AAPL")
    await cache_module.put(entry)

    result = await cache_module.get_fresh("AAPL", max_age_minutes=60)

    assert result is not None
    assert result.ticker == "AAPL"
    assert result.analysis.headline == "Test headline"


async def test_get_fresh_returns_none_on_miss(monkeypatch):
    _reset_memory_store()
    monkeypatch.setattr(cache_module, "get_settings", _fake_settings)

    result = await cache_module.get_fresh("UNKNOWN", max_age_minutes=60)

    assert result is None


# --- TTL boundary ---

async def test_get_fresh_ttl_boundary_just_inside_window(monkeypatch):
    _reset_memory_store()
    monkeypatch.setattr(cache_module, "get_settings", _fake_settings)

    now = datetime.now(timezone.utc)
    entry = _cached(ticker="MSFT", generated_at=now - timedelta(minutes=59))
    await cache_module.put(entry)

    result = await cache_module.get_fresh("MSFT", max_age_minutes=60)

    assert result is not None
    assert result.ticker == "MSFT"


async def test_get_fresh_ttl_boundary_just_outside_window(monkeypatch):
    _reset_memory_store()
    monkeypatch.setattr(cache_module, "get_settings", _fake_settings)

    now = datetime.now(timezone.utc)
    entry = _cached(ticker="MSFT", generated_at=now - timedelta(minutes=61))
    await cache_module.put(entry)

    result = await cache_module.get_fresh("MSFT", max_age_minutes=60)

    assert result is None


async def test_get_fresh_picks_newest_within_window(monkeypatch):
    _reset_memory_store()
    monkeypatch.setattr(cache_module, "get_settings", _fake_settings)

    now = datetime.now(timezone.utc)
    older = _cached(ticker="GOOG", generated_at=now - timedelta(minutes=30), change_pct=-1.0)
    newer = _cached(ticker="GOOG", generated_at=now - timedelta(minutes=5), change_pct=-2.0)
    await cache_module.put(older)
    await cache_module.put(newer)

    result = await cache_module.get_fresh("GOOG", max_age_minutes=60)

    assert result is not None
    assert result.change_pct == -2.0
