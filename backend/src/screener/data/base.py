"""Abstract base class for data providers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime


class DataProvider(ABC):
    @abstractmethod
    async def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str = "1d",
    ) -> list:
        """Fetch OHLCV bars for symbol between start and end (UTC datetimes)."""
        ...
