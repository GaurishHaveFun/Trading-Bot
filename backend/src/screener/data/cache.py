"""SQLite bar cache — persists OHLCV bars to avoid redundant network fetches."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from screener.models import Bar

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS bars (
    symbol    TEXT    NOT NULL,
    timestamp TEXT    NOT NULL,
    interval  TEXT    NOT NULL,
    open      REAL    NOT NULL,
    high      REAL    NOT NULL,
    low       REAL    NOT NULL,
    close     REAL    NOT NULL,
    volume    INTEGER NOT NULL,
    PRIMARY KEY (symbol, timestamp, interval)
)
"""


class BarCache:
    def __init__(self, db_path: str | Path = ".cache/bars.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute(_CREATE_TABLE)
        self._conn.commit()

    def get(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str = "1d",
    ) -> list[Bar]:
        """Return cached bars for symbol within [start, end] inclusive."""
        start_s = start.astimezone(timezone.utc).isoformat()
        end_s = end.astimezone(timezone.utc).isoformat()
        cur = self._conn.execute(
            """
            SELECT timestamp, open, high, low, close, volume
            FROM bars
            WHERE symbol = ? AND interval = ?
              AND DATE(timestamp) >= DATE(?) AND DATE(timestamp) <= DATE(?)
            ORDER BY timestamp
            """,
            (symbol, interval, start_s, end_s),
        )
        rows = cur.fetchall()
        return [
            Bar(
                timestamp=datetime.fromisoformat(row[0]),
                open=row[1],
                high=row[2],
                low=row[3],
                close=row[4],
                volume=row[5],
            )
            for row in rows
        ]

    def put(self, symbol: str, bars: list[Bar], interval: str = "1d") -> None:
        """Insert bars into the cache, ignoring duplicates."""
        self._put(symbol, bars, interval, verb="INSERT OR IGNORE")

    def put_replace(self, symbol: str, bars: list[Bar], interval: str = "1d") -> None:
        """Insert bars into the cache, overwriting any existing row with the same
        (symbol, timestamp, interval) primary key. Used to correct stale
        provisional bars (e.g. today's bar refetched after a late price revision)."""
        self._put(symbol, bars, interval, verb="INSERT OR REPLACE")

    def _put(self, symbol: str, bars: list[Bar], interval: str, verb: str) -> None:
        self._conn.executemany(
            f"""
            {verb} INTO bars
                (symbol, timestamp, interval, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    symbol,
                    bar.timestamp.astimezone(timezone.utc).isoformat(),
                    interval,
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.volume,
                )
                for bar in bars
            ],
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
