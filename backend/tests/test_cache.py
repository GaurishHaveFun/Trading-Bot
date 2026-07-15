"""Tests for the SQLite bar cache."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import pytest
from screener.data.cache import BarCache
from screener.models import Bar


def _make_bar(day: int, close: float = 100.0) -> Bar:
    return Bar(
        timestamp=datetime(2024, 1, day, 21, 0, 0, tzinfo=timezone.utc),
        open=close - 1,
        high=close + 2,
        low=close - 2,
        close=close,
        volume=1_000_000 * day,
    )


@pytest.fixture
def cache(tmp_path):
    c = BarCache(db_path=tmp_path / "test_bars.db")
    yield c
    c.close()


def test_put_and_get_all(cache):
    bars = [_make_bar(d, close=100.0 + d) for d in range(1, 11)]
    cache.put("AAPL", bars)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 31, tzinfo=timezone.utc)
    result = cache.get("AAPL", start, end)
    assert len(result) == 10


def test_get_subrange(cache):
    bars = [_make_bar(d) for d in range(1, 11)]
    cache.put("AAPL", bars)
    start = datetime(2024, 1, 3, tzinfo=timezone.utc)
    end = datetime(2024, 1, 7, tzinfo=timezone.utc)
    result = cache.get("AAPL", start, end)
    assert len(result) == 5
    assert result[0].timestamp.day == 3
    assert result[-1].timestamp.day == 7


def test_put_duplicate_ignored(cache):
    bar = _make_bar(1, close=100.0)
    cache.put("AAPL", [bar])
    cache.put("AAPL", [bar])  # duplicate — must not raise or double-insert
    result = cache.get("AAPL", datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc))
    assert len(result) == 1


def test_round_trip_values(cache):
    bar = _make_bar(5, close=192.31)
    cache.put("AAPL", [bar])
    result = cache.get("AAPL", datetime(2024, 1, 5, tzinfo=timezone.utc), datetime(2024, 1, 5, tzinfo=timezone.utc))
    assert len(result) == 1
    r = result[0]
    assert r.close == pytest.approx(192.31)
    assert r.volume == bar.volume
    assert r.timestamp.tzinfo == timezone.utc


def test_empty_result_for_missing_symbol(cache):
    result = cache.get("NVDA", datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 31, tzinfo=timezone.utc))
    assert result == []


def test_interval_isolation(cache):
    bar = _make_bar(1)
    cache.put("AAPL", [bar], interval="1d")
    cache.put("AAPL", [bar], interval="1h")
    result_d = cache.get("AAPL", datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc), interval="1d")
    result_h = cache.get("AAPL", datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc), interval="1h")
    assert len(result_d) == 1
    assert len(result_h) == 1


def test_put_replace_overwrites_existing_row(cache):
    bar_a = _make_bar(1, close=100.0)
    cache.put("AAPL", [bar_a])

    bar_b = _make_bar(1, close=124.0)  # same (symbol, timestamp, interval) key
    cache.put_replace("AAPL", [bar_b])

    result = cache.get(
        "AAPL",
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    assert len(result) == 1
    assert result[0].close == pytest.approx(124.0)


def test_autocreates_db_directory(tmp_path):
    nested = tmp_path / "a" / "b" / "c"
    cache = BarCache(db_path=nested / "bars.db")
    bar = _make_bar(1)
    cache.put("TSLA", [bar])
    result = cache.get("TSLA", datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc))
    assert len(result) == 1
    cache.close()
