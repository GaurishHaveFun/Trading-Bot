"""SQLite fundamentals cache — persists computed quality metrics so we only
recompute them once per UTC day per symbol."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from screener.models import FundamentalsSnapshot

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS fundamentals (
    symbol               TEXT    NOT NULL PRIMARY KEY,
    as_of                TEXT    NOT NULL,
    years_available      INTEGER NOT NULL,
    fcf_5y_cumulative    REAL,
    interest_coverage    REAL,
    gross_margin         REAL,
    ocf_ni_ratio         REAL,
    net_margin           REAL,
    share_dilution_5y    REAL
)
"""


class FundamentalsCache:
    def __init__(self, db_path: str | Path = ".cache/fundamentals.db") -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute(_CREATE_TABLE)
        self._conn.commit()

    def get(self, symbol: str) -> FundamentalsSnapshot | None:
        """Return the cached snapshot for symbol if it was computed today
        (UTC). Fundamentals only need refreshing once a day, so a snapshot
        from a previous UTC date is treated as stale and None is returned."""
        cur = self._conn.execute(
            """
            SELECT symbol, as_of, years_available, fcf_5y_cumulative,
                   interest_coverage, gross_margin, ocf_ni_ratio,
                   net_margin, share_dilution_5y
            FROM fundamentals
            WHERE symbol = ?
            """,
            (symbol,),
        )
        row = cur.fetchone()
        if row is None:
            return None

        as_of = datetime.fromisoformat(row[1])
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)
        else:
            as_of = as_of.astimezone(timezone.utc)

        if as_of.date() != datetime.now(timezone.utc).date():
            return None

        return FundamentalsSnapshot(
            ticker=row[0],
            as_of=as_of,
            years_available=row[2],
            fcf_5y_cumulative=row[3],
            interest_coverage=row[4],
            gross_margin=row[5],
            ocf_ni_ratio=row[6],
            net_margin=row[7],
            share_dilution_5y=row[8],
        )

    def put(self, symbol: str, snapshot: FundamentalsSnapshot) -> None:
        """Upsert the snapshot for symbol, keyed by symbol."""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO fundamentals
                (symbol, as_of, years_available, fcf_5y_cumulative,
                 interest_coverage, gross_margin, ocf_ni_ratio,
                 net_margin, share_dilution_5y)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol,
                snapshot.as_of.astimezone(timezone.utc).isoformat(),
                snapshot.years_available,
                snapshot.fcf_5y_cumulative,
                snapshot.interest_coverage,
                snapshot.gross_margin,
                snapshot.ocf_ni_ratio,
                snapshot.net_margin,
                snapshot.share_dilution_5y,
            ),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
