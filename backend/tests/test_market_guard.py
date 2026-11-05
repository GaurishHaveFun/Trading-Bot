"""Market-hours guard. No network."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from screener.api.snapshot_pusher import is_market_open


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


# --- is_market_open ---


def test_market_open_weekday_hours_dst():
    # Wed 2026-07-01 (EDT, UTC-4): 9:30 ET = 13:30 UTC
    assert not is_market_open(utc(2026, 7, 1, 13, 29))
    assert is_market_open(utc(2026, 7, 1, 13, 30))
    assert is_market_open(utc(2026, 7, 1, 20, 0))  # 16:00 inclusive
    assert not is_market_open(utc(2026, 7, 1, 20, 1))
    # Wed 2026-01-14 (EST, UTC-5): 9:30 ET = 14:30 UTC
    assert not is_market_open(utc(2026, 1, 14, 14, 29))
    assert is_market_open(utc(2026, 1, 14, 14, 30))
    assert is_market_open(utc(2026, 1, 14, 21, 0))
    assert not is_market_open(utc(2026, 1, 14, 21, 1))


def test_market_closed_weekend_and_dst_transition_days():
    assert not is_market_open(utc(2026, 7, 4, 16, 0))  # Saturday
    assert not is_market_open(utc(2026, 7, 5, 16, 0))  # Sunday
    # Mon 2026-03-09, first day of EDT: 13:30 UTC is open, 14:30 UTC (old EST open) too
    assert is_market_open(utc(2026, 3, 9, 13, 30))
    assert not is_market_open(utc(2026, 3, 9, 13, 29))
    # Mon 2026-11-02 EDT still; Mon 2026-11-02 -> DST ends Sun 11-01
    assert not is_market_open(utc(2026, 11, 2, 14, 29))
    assert is_market_open(utc(2026, 11, 2, 14, 30))


def test_market_requires_aware_datetime():
    with pytest.raises(ValueError):
        is_market_open(datetime(2026, 7, 1, 14, 0))
