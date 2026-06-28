"""Base class for universe providers."""
from __future__ import annotations
from abc import ABC, abstractmethod


class UniverseProvider(ABC):
    """Returns the list of ticker symbols to screen."""

    @abstractmethod
    def get_symbols(self) -> list[str]:
        """Return a list of ticker symbols."""
        ...
