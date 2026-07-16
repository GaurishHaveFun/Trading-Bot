"""Static universe provider — reads symbols from a YAML file."""
from __future__ import annotations
from pathlib import Path
import yaml
from screener.universe.base import UniverseProvider


class StaticUniverse(UniverseProvider):
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    async def get_symbols(self) -> list[str]:
        with open(self._path, "r") as f:
            data = yaml.safe_load(f)
        return [str(s) for s in data["symbols"]]
