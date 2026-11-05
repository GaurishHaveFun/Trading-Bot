"""SQLite store for Yahoo `info` snapshots pushed from a machine whose IP
Yahoo does not block (see `scripts/push_yahoo_snapshot.py`). Render's
datacenter IPs are blocked by Yahoo, so the API serves these stored
snapshots (plus live Schwab data) instead of calling yfinance itself.

Table `yahoo_info(symbol PK, info_json, fetched_at UTC ISO)`. All sqlite
work runs via `asyncio.to_thread`, on a short-lived connection per call so
no connection is shared across threads."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from screener.api.ticker_data import num
from screener.config import get_settings
from screener.utils.logging import get_logger

logger = get_logger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS yahoo_info (
    symbol     TEXT PRIMARY KEY,
    info_json  TEXT NOT NULL,
    fetched_at TEXT NOT NULL
)
"""

# Keys read by ticker_data.to_profile/to_stats/to_targets, plus a few extras
# (sector lookup for /losers, display names). Everything else is dropped to
# keep rows small.
ALLOWED_KEYS: frozenset[str] = frozenset(
    {
        # profile
        "longName", "shortName", "longBusinessSummary", "sector", "sectorDisp",
        "industry", "industryKey", "fullTimeEmployees", "website", "country",
        "fullExchangeName", "exchange", "currency",
        # stats
        "regularMarketPrice", "currentPrice", "regularMarketChangePercent",
        "regularMarketPreviousClose", "previousClose", "regularMarketOpen", "open",
        "regularMarketDayHigh", "dayHigh", "regularMarketDayLow", "dayLow",
        "marketCap", "trailingPE", "forwardPE", "priceToBook", "trailingEps",
        "forwardEps", "dividendYield", "trailingAnnualDividendYield", "beta",
        "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "regularMarketVolume", "volume",
        "averageVolume", "averageVolume10days", "averageDailyVolume10Day",
        # targets
        "targetMeanPrice", "targetHighPrice", "targetLowPrice", "targetMedianPrice",
        "recommendationKey", "numberOfAnalystOpinions",
    }
)

_MAX_STRING_LEN = 5000


def sanitize_info(info: dict) -> dict:
    """Keep only allow-listed keys with JSON-safe values: strings (truncated),
    bools, and finite numbers. NaN/inf/None/containers are dropped."""
    clean: dict = {}
    for key, value in info.items():
        if key not in ALLOWED_KEYS or value is None:
            continue
        if isinstance(value, bool):
            clean[key] = value
        elif isinstance(value, str):
            clean[key] = value[:_MAX_STRING_LEN]
        elif isinstance(value, (int, float)):
            n = num(value)
            if n is None:
                continue
            clean[key] = value if isinstance(value, int) else n
    return clean


class YahooSnapshotStore:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db_path), timeout=10)

    def _upsert_many_sync(self, snapshots: dict[str, dict]) -> int:
        now = datetime.now(timezone.utc).isoformat()
        rows = []
        for symbol, info in snapshots.items():
            if not isinstance(info, dict):
                continue
            clean = sanitize_info(info)
            if not clean:
                continue
            rows.append((symbol.strip().upper(), json.dumps(clean), now))
        if not rows:
            return 0
        conn = self._connect()
        try:
            with conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO yahoo_info (symbol, info_json, fetched_at) VALUES (?, ?, ?)",
                    rows,
                )
        finally:
            conn.close()
        return len(rows)

    def _get_sync(self, symbol: str) -> tuple[dict, datetime] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT info_json, fetched_at FROM yahoo_info WHERE symbol = ?",
                (symbol.strip().upper(),),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return json.loads(row[0]), datetime.fromisoformat(row[1])

    async def upsert_many(self, snapshots: dict[str, dict]) -> int:
        """Sanitize and store; returns the number of rows written."""
        return await asyncio.to_thread(self._upsert_many_sync, snapshots)

    async def get(self, symbol: str) -> tuple[dict, datetime] | None:
        """Never raises: any read failure is logged and yields None."""
        try:
            return await asyncio.to_thread(self._get_sync, symbol)
        except Exception as exc:
            logger.warning("yahoo_snapshot_read_error", ticker=symbol, error=str(exc))
            return None


_store: YahooSnapshotStore | None = None
_store_path: str | None = None


def get_store() -> YahooSnapshotStore:
    """Process-wide store for the configured path (rebuilt if the setting
    changes, which keeps tests hermetic)."""
    global _store, _store_path
    path = get_settings().yahoo_snapshot_db_path
    if _store is None or _store_path != path:
        _store = YahooSnapshotStore(path)
        _store_path = path
    return _store
