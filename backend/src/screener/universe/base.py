"""Base class for universe providers."""
from __future__ import annotations
from abc import ABC, abstractmethod


class UniverseProvider(ABC):
    """Returns the list of ticker symbols to screen."""

    @abstractmethod
    def get_symbols(self) -> list[str]:
        """Return a list of ticker symbols."""
        ...

    def get_quotes(self) -> dict[str, dict]:
        """Return per-symbol quote metadata (e.g. price_to_book, change_pct,
        market_cap) keyed by ticker symbol. Default: no metadata, so existing
        providers are unaffected."""
        return {}
