"""yfinance data provider with SQLite cache integration."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

from screener.data.base import DataProvider
from screener.data.cache import BarCache
from screener.models import Bar
from screener.utils.logging import get_logger

logger = get_logger(__name__)

_FETCH_DELAY = 0.2  # seconds between live fetches


class YFinanceProvider(DataProvider):
    def __init__(self, cache: BarCache) -> None:
        self._cache = cache

    async def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str = "1d",
    ) -> list[Bar]:
        """Return bars from cache, fetching only missing ranges from yfinance."""
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
        """Fetch bars from yfinance via asyncio.to_thread."""
        await asyncio.sleep(_FETCH_DELAY)
        logger.info("fetching_bars", symbol=symbol, start=str(start.date()), end=str(end.date()))
        bars = await asyncio.to_thread(self._download, symbol, start, end, interval)
        return bars

    def _download(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str,
    ) -> list[Bar]:
        """Blocking yfinance download — call via asyncio.to_thread only."""
        df = yf.download(
            symbol,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            interval=interval,
            auto_adjust=True,
            progress=False,
            multi_level_index=False,
        )
        if df.empty:
            return []
        return self._df_to_bars(df)

    def _df_to_bars(self, df: pd.DataFrame) -> list[Bar]:
        """Convert yfinance DataFrame to list of Bar objects with UTC timestamps."""
        bars = []
        for ts, row in df.iterrows():
            # yfinance timestamps may be tz-aware or tz-naive depending on version
            if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
                dt = ts.to_pydatetime().astimezone(timezone.utc)
            else:
                dt = ts.to_pydatetime().replace(tzinfo=timezone.utc)
            bars.append(
                Bar(
                    timestamp=dt,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=int(row["Volume"]),
                )
            )
        return bars
