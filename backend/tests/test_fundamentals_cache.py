"""Tests for the SQLite fundamentals cache."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from screener.data.fundamentals_cache import FundamentalsCache
from screener.models import FundamentalsSnapshot


def _make_snapshot(as_of: datetime, ticker: str = "AAPL") -> FundamentalsSnapshot:
    return FundamentalsSnapshot(
        ticker=ticker,
        as_of=as_of,
        years_available=5,
        fcf_5y_cumulative=1000.0,
        interest_coverage=12.5,
        gross_margin=0.42,
        ocf_ni_ratio=1.1,
        net_margin=0.21,
        share_dilution_5y=0.03,
    )


@pytest.fixture
def cache(tmp_path):
    c = FundamentalsCache(db_path=tmp_path / "test_fundamentals.db")
    yield c
    c.close()


def test_put_and_get_today_returns_snapshot(cache):
    snap = _make_snapshot(datetime.now(timezone.utc))
    cache.put("AAPL", snap)

    result = cache.get("AAPL")
    assert result is not None
    assert result.ticker == "AAPL"
    assert result.years_available == 5
    assert result.fcf_5y_cumulative == pytest.approx(1000.0)
    assert result.interest_coverage == pytest.approx(12.5)
    assert result.gross_margin == pytest.approx(0.42)
    assert result.ocf_ni_ratio == pytest.approx(1.1)
    assert result.net_margin == pytest.approx(0.21)
    assert result.share_dilution_5y == pytest.approx(0.03)
    assert result.as_of.tzinfo == timezone.utc


def test_get_returns_none_for_stale_as_of(cache):
    stale = datetime.now(timezone.utc) - timedelta(days=1)
    snap = _make_snapshot(stale)
    cache.put("AAPL", snap)

    result = cache.get("AAPL")
    assert result is None


def test_get_returns_none_for_missing_symbol(cache):
    assert cache.get("NVDA") is None


def test_put_upserts_on_symbol(cache):
    first = _make_snapshot(datetime.now(timezone.utc))
    cache.put("AAPL", first)

    second = _make_snapshot(datetime.now(timezone.utc))
    second = second.model_copy(update={"years_available": 3, "gross_margin": None})
    cache.put("AAPL", second)

    result = cache.get("AAPL")
    assert result.years_available == 3
    assert result.gross_margin is None


def test_none_metrics_round_trip(cache):
    snap = FundamentalsSnapshot(
        ticker="ZZZZ",
        as_of=datetime.now(timezone.utc),
        years_available=0,
        fcf_5y_cumulative=None,
        interest_coverage=None,
        gross_margin=None,
        ocf_ni_ratio=None,
        net_margin=None,
        share_dilution_5y=None,
    )
    cache.put("ZZZZ", snap)
    result = cache.get("ZZZZ")
    assert result.years_available == 0
    assert result.fcf_5y_cumulative is None
    assert result.interest_coverage is None
    assert result.gross_margin is None
    assert result.ocf_ni_ratio is None
    assert result.net_margin is None
    assert result.share_dilution_5y is None


def test_autocreates_db_directory(tmp_path):
    nested = tmp_path / "a" / "b" / "c"
    cache = FundamentalsCache(db_path=nested / "fundamentals.db")
    snap = _make_snapshot(datetime.now(timezone.utc))
    cache.put("AAPL", snap)
    result = cache.get("AAPL")
    assert result is not None
    cache.close()


def test_close_does_not_error(cache):
    cache.close()
