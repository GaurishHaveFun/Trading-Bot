"""S&P 500 universe provider — Wikipedia scrape with 24h file cache."""
from __future__ import annotations
import json
import time
from pathlib import Path
import pandas as pd
from screener.universe.base import UniverseProvider

_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
_CACHE_TTL = 86400  # 24 hours in seconds


class SP500Universe(UniverseProvider):
    def __init__(self, cache_dir: str | Path = ".cache") -> None:
        self._cache_path = Path(cache_dir) / "sp500.json"

    def get_symbols(self) -> list[str]:
        if self._cache_valid():
            return self._load_cache()
        symbols = self._fetch()
        self._save_cache(symbols)
        return symbols

    def _cache_valid(self) -> bool:
        if not self._cache_path.exists():
            return False
        age = time.time() - self._cache_path.stat().st_mtime
        return age < _CACHE_TTL

    def _load_cache(self) -> list[str]:
        with open(self._cache_path, "r") as f:
            return json.load(f)

    def _save_cache(self, symbols: list[str]) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._cache_path, "w") as f:
            json.dump(symbols, f)

    def _fetch(self) -> list[str]:
        tables = pd.read_html(_WIKI_URL)
        df = tables[0]
        return df["Symbol"].str.replace(".", "-", regex=False).tolist()
