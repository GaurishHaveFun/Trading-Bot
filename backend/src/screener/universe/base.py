"""Base class for universe providers."""
from __future__ import annotations
from abc import ABC, abstractmethod


class UniverseProvider(ABC):
    """Returns the list of ticker symbols to screen. All methods are async:
    Schwab-backed providers have no synchronous HTTP path, so this interface
    is uniformly async across every concrete provider (static, yfinance-
    losers, schwab-losers) — every call site awaits these."""

    @abstractmethod
    async def get_symbols(self) -> list[str]:
        """Return a list of ticker symbols."""
        ...

    async def get_quotes(self) -> dict[str, dict]:
        """Return per-symbol quote metadata (e.g. price_to_book, change_pct,
        market_cap) keyed by ticker symbol. Default: no metadata, so
        providers that don't fetch quotes (e.g. StaticUniverse) are
        unaffected."""
        return {}

    async def quote_for(self, symbol: str) -> dict | None:
        """Fetch and shape quote metadata for a single arbitrary symbol
        (used by --ticker debug mode). Default: no quote data available."""
        return None
