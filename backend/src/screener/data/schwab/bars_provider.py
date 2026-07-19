"""Schwab Trader API bar data provider with SQLite cache integration.

Mirrors `screener.data.yfinance_provider.YFinanceProvider`'s exact
cache-check / head-backfill / tail-backfill / same-day-refresh control flow
(see that module for the rationale on the same-day refresh — a same-day
cache hit otherwise pins a stale pre-close price for the rest of the day),
swapping the underlying fetch mechanism from yfinance to the Schwab
price-history endpoint via `SchwabClient`.

`_fetch()` goes through `SchwabClient.call()` with schwab-py's typed
`AsyncClient.get_price_history()` rather than the legacy raw-path `get()`.
Two schwab-py enum quirks to note for future readers:

  - `enforce_enums` defaults to `True` on the `AsyncClient` schwab-py builds
    for us (via `client_from_token_file` in `auth.py`, which doesn't
    override the default) — so `frequency`/`period_type`/`frequency_type`
    MUST be passed as schwab-py enum members, not raw ints/strs, or
    `convert_enum` raises.
  - schwab-py's `PriceHistory.Frequency` enum only has minute-based members
    (`EVERY_MINUTE = 1`, `EVERY_FIVE_MINUTES = 5`, ...) — there is no
    "daily" member. schwab-py's own bundled convenience wrapper,
    `BaseClient.get_price_history_every_day()`, resolves this by passing
    `frequency=PriceHistory.Frequency.EVERY_MINUTE` (value `1`) alongside
    `frequency_type=PriceHistory.FrequencyType.DAILY` — confirmed by reading
    that method's source in the installed package
    (`.venv/lib/python3.12/site-packages/schwab/client/base.py`). We follow
    the same pattern here rather than calling that convenience wrapper
    directly, since it hardcodes `period=Period.TWENTY_YEARS` and defaults
    `start_datetime`/`end_datetime` to `None`, whereas we need explicit
    start/end control for cache head/tail backfills.
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

        # NOTE (originally UNVERIFIED, now CONFIRMED against the real Schwab
        # OpenAPI spec pasted in from developer.schwab.com): this
        # periodType/frequencyType/frequency + explicit startDate/endDate +
        # needExtendedHoursData shape is CONFIRMED CORRECT. periodType="year"
        # allows frequencyType of daily/weekly/monthly, and
        # frequencyType="daily" requires frequency=1. See the module
        # docstring for why frequency=1 is expressed as schwab-py's
        # `PriceHistory.Frequency.EVERY_MINUTE` enum member below (its value
        # is `1`; `enforce_enums=True` rejects a raw int). The response
        # shape is likewise CONFIRMED CORRECT: a CandleList with top-level
        # candles/empty/previousClose/previousCloseDate/
        # previousCloseDateISO8601/symbol, where each Candle has open/high/
        # low/close/datetime (epoch ms)/datetimeISO8601/volume — exactly
        # what `_candles_to_bars` below already reads. schwab-py's
        # `get_price_history` converts `start_datetime`/`end_datetime` to
        # epoch millis internally, so `start`/`end` are passed through
        # as-is.
        price_history = self._client.raw.PriceHistory
        payload = await self._client.call(
            lambda: self._client.raw.get_price_history(
                symbol,
                period_type=price_history.PeriodType.YEAR,
                frequency_type=price_history.FrequencyType.DAILY,
                frequency=price_history.Frequency.EVERY_MINUTE,
                start_datetime=start,
                end_datetime=end,
                need_extended_hours_data=False,
            ),
            label="get_price_history",
        )
        return self._candles_to_bars(payload)

    def _candles_to_bars(self, payload: dict) -> list[Bar]:
        """Convert a Schwab price-history response payload's `candles` array
        into `list[Bar]`, dropping (not fabricating) any candle whose open/
        high/low/close is missing/None/NaN. A missing or empty `candles` key
        yields an empty list.

        The CandleList/Candle response shape this reads (candles/empty/
        previousClose/previousCloseDate/previousCloseDateISO8601/symbol at
        the top level; open/high/low/close/datetime(epoch ms)/
        datetimeISO8601/volume per candle) is CONFIRMED CORRECT against the
        real Schwab OpenAPI spec — see the NOTE in `_fetch` above."""
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
