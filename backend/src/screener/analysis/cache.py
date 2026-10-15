"""TTL cache for `TickerAnalysis` results, keyed by ticker. Uses Postgres
(same psycopg/Jsonb idiom as `screener.output.db_writer`) when
`settings.database_url` is set, otherwise falls back to an in-process dict so
local dev without a DB still works."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from screener.analysis.models import CachedAnalysis, TickerAnalysis
from screener.config import get_settings
from screener.utils.logging import get_logger

logger = get_logger(__name__)

_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS ticker_analyses (
        id BIGSERIAL PRIMARY KEY,
        ticker TEXT NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        model TEXT NOT NULL,
        change_pct DOUBLE PRECISION,
        analysis JSONB NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ticker_analyses_ticker_time ON ticker_analyses (ticker, generated_at DESC)",
)

# In-process fallback store used only when settings.database_url is blank.
# Keyed by ticker -> list of CachedAnalysis (append-only in `put`, filtered
# by TTL window in `get_fresh`).
_memory_store: dict[str, list[CachedAnalysis]] = {}


async def _ensure_schema(conn: AsyncConnection) -> None:
    for statement in _SCHEMA_STATEMENTS:
        await conn.execute(statement)


async def get_fresh(ticker: str, max_age_minutes: int) -> CachedAnalysis | None:
    """Return the freshest cached analysis for `ticker` within
    `max_age_minutes`, or None on a miss (including any cache-read
    failure -- this never raises)."""
    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)

    if not settings.database_url:
        entries = [c for c in _memory_store.get(ticker, []) if c.generated_at >= cutoff]
        if not entries:
            return None
        return max(entries, key=lambda c: c.generated_at)

    try:
        async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
            await _ensure_schema(conn)
            cur = await conn.execute(
                """
                SELECT ticker, generated_at, model, change_pct, analysis
                FROM ticker_analyses
                WHERE ticker = %s AND generated_at >= %s
                ORDER BY generated_at DESC
                LIMIT 1
                """,
                (ticker, cutoff),
            )
            row = await cur.fetchone()
    except Exception as exc:
        logger.warning("analysis_cache_read_error", ticker=ticker, error=str(exc))
        return None

    if row is None:
        return None

    return CachedAnalysis(
        ticker=row[0],
        generated_at=row[1],
        model=row[2],
        change_pct=row[3],
        analysis=TickerAnalysis.model_validate(row[4]),
    )


async def put(cached: CachedAnalysis) -> None:
    """Persist `cached`. Never raises -- a DB write failure is logged and
    the write is simply skipped (degrades to a miss on the next read)."""
    settings = get_settings()

    if not settings.database_url:
        _memory_store.setdefault(cached.ticker, []).append(cached)
        return

    try:
        async with await psycopg.AsyncConnection.connect(settings.database_url) as conn:
            await _ensure_schema(conn)
            await conn.execute(
                """
                INSERT INTO ticker_analyses (ticker, generated_at, model, change_pct, analysis)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    cached.ticker,
                    cached.generated_at,
                    cached.model,
                    cached.change_pct,
                    Jsonb(cached.analysis.model_dump(mode="json")),
                ),
            )
    except Exception as exc:
        logger.warning("analysis_cache_write_error", ticker=cached.ticker, error=str(exc))
        return
