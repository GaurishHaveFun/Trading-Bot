"""Logic behind `scripts/push_yahoo_snapshot.py`: build the ticker list, fetch
yfinance `info` for each (bounded concurrency), and POST it in chunks to the
deployed API's `/internal/yahoo-snapshot`. Runs on a machine whose IP Yahoo
does not block."""
from __future__ import annotations

import asyncio
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import httpx

from screener.api.ticker_data import fetch_quote_info, looks_like_real_symbol
from screener.universe.losers import LosersUniverse
from screener.utils.logging import get_logger

logger = get_logger(__name__)

NY = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)

CHUNK_SIZE = 50
MAX_LOSERS = 60


def is_market_open(now_utc: datetime) -> bool:
    """True Mon-Fri 9:30-16:00 America/New_York inclusive of both endpoints.
    US market holidays are NOT handled."""
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    ny = now_utc.astimezone(NY)
    if ny.weekday() >= 5:
        return False
    return MARKET_OPEN <= ny.time().replace(tzinfo=None) <= MARKET_CLOSE


def is_market_open_now() -> bool:
    return is_market_open(datetime.now(timezone.utc))


def chunked(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def rank_losers(quotes: dict[str, dict], limit: int = MAX_LOSERS) -> list[str]:
    """Symbols with the most negative `change_pct` first, capped at `limit`."""
    ranked = sorted(quotes.items(), key=lambda kv: kv[1].get("change_pct") or 0.0)
    return [sym for sym, _ in ranked[:limit]]


async def build_ticker_list(watchlist: set[str], max_losers: int = MAX_LOSERS) -> list[str]:
    """Watchlist (sorted) followed by the current top losers, de-duplicated."""
    symbols = sorted(s.upper() for s in watchlist)
    try:
        universe = LosersUniverse(watchlist=watchlist)
        await universe.get_symbols()
        losers = rank_losers(await universe.get_quotes(), max_losers)
    except Exception as exc:
        logger.warning("push_losers_unavailable", error=str(exc))
        losers = []
    return list(dict.fromkeys(symbols + [s.upper() for s in losers]))


async def fetch_infos(symbols: list[str], concurrency: int = 10) -> dict[str, dict]:
    """yfinance info per symbol; failures and junk results are skipped."""
    sem = asyncio.Semaphore(concurrency)

    async def one(symbol: str) -> tuple[str, dict | None]:
        async with sem:
            try:
                info = await asyncio.to_thread(fetch_quote_info, symbol)
            except Exception as exc:
                logger.warning("push_fetch_error", ticker=symbol, error=str(exc))
                return symbol, None
        if not info or not looks_like_real_symbol(info):
            return symbol, None
        return symbol, info

    results = await asyncio.gather(*(one(s) for s in symbols))
    return {sym: info for sym, info in results if info}


async def push_infos(
    infos: dict[str, dict],
    target_url: str,
    secret: str,
    *,
    chunk_size: int = CHUNK_SIZE,
    client: httpx.AsyncClient | None = None,
) -> int:
    """POST `infos` in chunks. Returns the number of symbols the server
    accepted; failed chunks are logged and skipped."""
    url = target_url.rstrip("/") + "/internal/yahoo-snapshot"
    headers = {"X-Push-Secret": secret}
    own = client is None
    http = client or httpx.AsyncClient(timeout=60)
    pushed = 0
    try:
        for chunk in chunked(list(infos), chunk_size):
            payload = {"snapshots": {s: infos[s] for s in chunk}}
            try:
                resp = await http.post(url, json=payload, headers=headers)
            except Exception as exc:
                logger.warning("push_chunk_error", error=str(exc))
                continue
            if resp.is_success:
                pushed += int(resp.json().get("stored", len(chunk)))
            else:
                logger.warning("push_chunk_rejected", status=resp.status_code)
    finally:
        if own:
            await http.aclose()
    return pushed
