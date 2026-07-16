"""Schwab Trader API bar data provider with SQLite cache integration.

Mirrors `screener.data.yfinance_provider.YFinanceProvider`'s exact
cache-check / head-backfill / tail-backfill / same-day-refresh control flow
(see that module for the rationale on the same-day refresh — a same-day
cache hit otherwise pins a stale pre-close price for the rest of the day),
swapping the underlying fetch mechanism from yfinance to the Schwab
price-history endpoint via `SchwabClient`.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from screener.data.base import DataProvider
from screener.data.cache import BarCache
from screener.data.schwab.client import SchwabClient
from screener.models import Bar
from screener.utils.logging import get_logger

logger = get_logger(__name__)

_PRICE_HISTORY_PATH = "/marketdata/v1/pricehistory"


def _to_epoch_millis(dt: datetime) -> int:
    """Convert a UTC-ish datetime to epoch milliseconds, as required by
    Schwab's price-history `startDate`/`endDate` query params."""
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)


def _is_missing_or_nan(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return False


class SchwabProvider(DataProvider):
    def __init__(self, client: SchwabClient, cache: BarCache) -> None:
        self._client = client
        self._cache = cache

    async def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str = "1d",
    ) -> list[Bar]:
        """Return bars from cache, fetching only missing ranges from Schwab."""
        cached = self._cache.get(symbol, start, end, interval)

        if cached:
            cached_start = cached[0].timestamp
            cached_end = cached[-1].timestamp

            # Fetch missing head (compare dates only — bars are stored at market-close time)
            if start.astimezone(timezone.utc).date() < cached_start.date():
                head = await self._fetch(symbol, start, cached_start, interval)
                if head:
                    self._cache.put(symbol, head, interval)

            # Fetch missing tail (compare dates only)
            if end.astimezone(timezone.utc).date() > cached_end.date():
                tail = await self._fetch(symbol, cached_end, end, interval)
                if tail:
                    self._cache.put(symbol, tail, interval)

            # Refresh today's bar if we already had a provisional row for it —
            # a same-day cache hit otherwise pins the stale pre-close price for
            # the rest of the day (see backend/CLAUDE.md bug notes).
            today = datetime.now(timezone.utc).date()
            requested_start = start.astimezone(timezone.utc).date()
            requested_end = end.astimezone(timezone.utc).date()
            if requested_start <= today <= requested_end and cached_end.date() == today:
                today_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
                refreshed = await self._fetch(symbol, today_start, end, interval)
                if refreshed:
                    logger.info("refreshing_today_bar", symbol=symbol, date=str(today))
                    self._cache.put_replace(symbol, refreshed, interval)

            # Re-query to get the full merged result
            return self._cache.get(symbol, start, end, interval)

        # Nothing cached — fetch everything
        bars = await self._fetch(symbol, start, end, interval)
        if bars:
            self._cache.put(symbol, bars, interval)
        return bars

    async def _fetch(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str = "1d",
    ) -> list[Bar]:
        """Fetch bars from the Schwab price-history endpoint via SchwabClient."""
        if interval != "1d":
            raise ValueError(
                f"SchwabProvider currently only supports interval='1d', got {interval!r}"
            )

        logger.info("fetching_bars", symbol=symbol, start=str(start.date()), end=str(end.date()))

        params = {
            "symbol": symbol,
            # NOTE: the exact query-param shape below (periodType/frequencyType/
            # frequency alongside explicit startDate/endDate, plus
            # needExtendedHoursData) is a best-effort, UNVERIFIED assumption
            # based on publicly documented conventions for Schwab's Trader API
            # price-history endpoint. It has not been exercised against a real
            # response yet (no live credentials this session) and should be
            # corrected against the actual API docs/responses once available.
            "periodType": "year",
            "frequencyType": "daily",
            "frequency": 1,
            "startDate": _to_epoch_millis(start),
            "endDate": _to_epoch_millis(end),
            "needExtendedHoursData": False,
        }
        payload = await self._client.get(_PRICE_HISTORY_PATH, params)
        return self._candles_to_bars(payload)

    def _candles_to_bars(self, payload: dict) -> list[Bar]:
        """Convert a Schwab price-history response payload's `candles` array
        into `list[Bar]`, dropping (not fabricating) any candle whose open/
        high/low/close is missing/None/NaN. A missing or empty `candles` key
        yields an empty list."""
        candles = payload.get("candles") or []
        bars: list[Bar] = []
        for candle in candles:
            open_ = candle.get("open")
            high = candle.get("high")
            low = candle.get("low")
            close = candle.get("close")

            if any(_is_missing_or_nan(v) for v in (open_, high, low, close)):
                logger.info("dropping_candle_missing_ohlc", candle=candle)
                continue

            ts_ms = candle.get("datetime")
            if ts_ms is None:
                logger.info("dropping_candle_missing_timestamp", candle=candle)
                continue

            timestamp = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            volume = candle.get("volume") or 0

            bars.append(
                Bar(
                    timestamp=timestamp,
                    open=float(open_),
                    high=float(high),
                    low=float(low),
                    close=float(close),
                    volume=int(volume),
                )
            )
        return bars
