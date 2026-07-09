"""Losers universe provider — pulls today's top daily losers from yfinance,
unioned with watchlist (big-tech/chip) symbols that are down today."""
from __future__ import annotations

import yfinance as yf

from screener.universe.base import UniverseProvider
from screener.utils.logging import get_logger

logger = get_logger(__name__)

_MIN_MARKET_CAP = 10_000_000_000  # $10B cap floor — drop penny/small-cap losers
_SCREEN_COUNT = 50
_TOP_N = 15


class LosersUniverse(UniverseProvider):
    """Screens yfinance's `day_losers` predefined query and unions the result
    with any watchlist symbols that are also down today."""

    def __init__(self, watchlist: set[str] | None = None) -> None:
        self._watchlist = watchlist or set()
        self._quotes: dict[str, dict] = {}

    def get_symbols(self) -> list[str]:
        """Top-15 (by % loss) large-cap equity losers, unioned with watchlist
        symbols currently down today. Also stashes quote metadata for
        `get_quotes()`, fetched once per call."""
        raw_quotes = self._fetch_losers()
        top = self._filter_and_rank(raw_quotes)
        watchlist_down = self._watchlist_down_today()

        quotes: dict[str, dict] = {}
        for q in top:
            symbol = q.get("symbol")
            if symbol:
                quotes[symbol] = self._to_meta(q)
        for symbol, q in watchlist_down.items():
            quotes.setdefault(symbol, self._to_meta(q))

        self._quotes = quotes
        return sorted(quotes.keys())

    def get_quotes(self) -> dict[str, dict]:
        """Per-symbol metadata (price_to_book, change_pct, market_cap) for the
        symbols returned by the most recent `get_symbols()` call."""
        return dict(self._quotes)

    def quote_for(self, symbol: str) -> dict | None:
        """Fetch and shape quote metadata for a single arbitrary symbol
        (used by `--ticker` debug mode, which may target a name outside the
        current day's losers screen)."""
        info = self._fetch_quote(symbol)
        if info is None:
            return None
        return self._to_meta(info)

    def _fetch_losers(self) -> list[dict]:
        """Call `yf.screen("day_losers", ...)` and return the raw quote list."""
        try:
            result = yf.screen("day_losers", count=_SCREEN_COUNT)
        except Exception as exc:
            logger.warning("losers_screen_error", error=str(exc))
            return []
        if not isinstance(result, dict):
            return []
        return result.get("quotes", []) or []

    def _filter_and_rank(self, quotes: list[dict]) -> list[dict]:
        """Drop non-EQUITY and sub-$10B market cap names, then take the top
        15 by % loss (most negative regularMarketChangePercent first)."""
        filtered = [
            q
            for q in quotes
            if q.get("quoteType") == "EQUITY"
            and (q.get("marketCap") or 0) >= _MIN_MARKET_CAP
        ]
        filtered.sort(key=lambda q: q.get("regularMarketChangePercent") or 0.0)
        return filtered[:_TOP_N]

    def _watchlist_down_today(self) -> dict[str, dict]:
        """Fetch quotes for watchlist symbols and keep only those down today."""
        down: dict[str, dict] = {}
        for symbol in sorted(self._watchlist):
            info = self._fetch_quote(symbol)
            if info is None:
                continue
            change_pct = info.get("regularMarketChangePercent")
            if change_pct is not None and change_pct < 0:
                down[symbol] = info
        return down

    def _fetch_quote(self, symbol: str) -> dict | None:
        """Fetch a single ticker's info dict. Isolated for easy mocking/testing."""
        try:
            return yf.Ticker(symbol).info
        except Exception as exc:
            logger.warning("watchlist_quote_error", symbol=symbol, error=str(exc))
            return None

    @staticmethod
    def _to_meta(q: dict) -> dict:
        return {
            "price_to_book": q.get("priceToBook"),
            "change_pct": q.get("regularMarketChangePercent"),
            "market_cap": q.get("marketCap"),
            "industry": q.get("industry") or q.get("industryKey"),
            "sector": q.get("sector"),
        }
