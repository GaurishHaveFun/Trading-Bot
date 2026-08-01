"""Read-only FastAPI HTTP service for a separate Next.js frontend: live-ish
quotes and today's top losers. Routes are thin wiring only — every fetch/
parse of yfinance or Schwab data is delegated to the existing provider/
universe classes (`screener.universe.losers.LosersUniverse`,
`screener.data.schwab.universe.SchwabLosersUniverse`). The only new logic
here is (a) merging/deduping the two losers sources and (b) attaching a
live `price` field, neither of which exists anywhere else in the codebase
today — see the two docstrings below for why.

Deviation from the original spec worth flagging explicitly: neither
`LosersUniverse`/`SchwabLosersUniverse`'s `get_quotes()`/`quote_for()` (the
locked `_to_meta` shape: `price_to_book`, `change_pct`, `market_cap`,
`industry`, `sector`) nor `YFinanceProvider` (OHLCV bars only) expose a live
`price`/`previous_close`/`currency` field anywhere — those simply aren't
part of any existing provider's public contract. Rather than invent a new
fetch mechanism, `_fetch_quote_info` below reuses the exact single-symbol
lookup pattern already established twice in this codebase
(`LosersUniverse._fetch_quote` and `screener.data.factory._enrich_industry_sector`,
both of which call `yf.Ticker(symbol).info` directly) and simply reads the
additional keys (`regularMarketPrice`/`currentPrice`, `regularMarketPreviousClose`/
`previousClose`, `currency`) off that same info dict. No new HTTP call shape
or parsing logic is introduced — this mirrors an existing, already-reviewed
pattern rather than reimplementing anything."""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

import yfinance as yf
from fastapi import FastAPI, Query
from pydantic import BaseModel

from screener.config import get_settings, load_watchlist
from screener.data.schwab.auth import SchwabAuth
from screener.data.schwab.client import SchwabClient
from screener.data.schwab.universe import SchwabLosersUniverse
from screener.universe.losers import LosersUniverse
from screener.utils.logging import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup hook: on Render's ephemeral filesystem there is no
    `.cache/schwab_token.json` on disk after a deploy, so the weekly-refreshed
    token is instead pushed into the `SCHWAB_TOKEN_JSON` env var (see
    `backend/scripts/schwab_reauth.py`) and materialized to disk here, before
    any request handler can reach for a Schwab client. Reads the env var
    directly (not via `Settings` — it's a secret blob, not config) and calls
    `get_settings()` at call time (not import time) so tests can monkeypatch
    it."""
    token_json = os.environ.get("SCHWAB_TOKEN_JSON", "").strip()
    if token_json:
        settings = get_settings()
        token_path = Path(settings.schwab_token_path)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(token_json)
        logger.info("schwab_token_materialized_from_env", token_path=str(token_path))
    else:
        logger.debug("schwab_token_env_not_set")
    yield


app = FastAPI(title="Trading Bot API", lifespan=lifespan)

_QUOTE_SEMAPHORE = asyncio.Semaphore(10)
_DEFAULT_LOSERS_LIMIT = 20


class QuoteOut(BaseModel):
    ticker: str
    price: float | None
    change_pct: float | None
    previous_close: float | None
    currency: str | None


class LoserOut(BaseModel):
    ticker: str
    price: float | None
    change_pct: float | None
    market_cap: float | None
    sector: str | None


def _fetch_quote_info(symbol: str) -> dict | None:
    """Blocking single-symbol yfinance lookup. Mirrors
    `LosersUniverse._fetch_quote`/`factory._enrich_industry_sector`'s
    identical `yf.Ticker(symbol).info` pattern — call via `asyncio.to_thread`
    only. Returns `None` on any fetch error (logged by the caller)."""
    return yf.Ticker(symbol).info


async def _fetch_quote(symbol: str) -> dict | None:
    """Async, semaphore-bounded wrapper around `_fetch_quote_info`. Returns
    `None` (and logs a structlog warning) on any fetch failure or empty
    result rather than raising, so one bad symbol never fails a whole
    request."""
    async with _QUOTE_SEMAPHORE:
        try:
            info = await asyncio.to_thread(_fetch_quote_info, symbol)
        except Exception as exc:
            logger.warning("quote_fetch_error", ticker=symbol, error=str(exc))
            return None
    if not info:
        logger.warning("quote_fetch_empty", ticker=symbol)
        return None
    return info


def merge_losers_quotes(
    yfinance_quotes: dict[str, dict],
    schwab_quotes: dict[str, dict],
    limit: int,
) -> list[tuple[str, dict]]:
    """Merge+dedupe two `{ticker: meta}` quote dicts (the shape returned by
    `LosersUniverse.get_quotes()`/`SchwabLosersUniverse.get_quotes()`) by
    ticker symbol — Schwab wins on overlap, matching
    `screener.data.factory._MergedLosersUniverse`'s existing precedent for
    how these two sources are combined elsewhere in this codebase. Ranks by
    `change_pct` ascending (biggest loss first; missing values sort as 0.0)
    and caps at `limit`. Pure function — no I/O — so it's directly unit
    testable without mocking any network calls."""
    merged = dict(yfinance_quotes)
    merged.update(schwab_quotes)
    ranked = sorted(merged.items(), key=lambda kv: kv[1].get("change_pct") or 0.0)
    return ranked[:limit]


async def _fetch_yfinance_losers_quotes(watchlist: set[str]) -> dict[str, dict]:
    universe = LosersUniverse(watchlist=watchlist)
    await universe.get_symbols()
    return await universe.get_quotes()


async def _fetch_schwab_losers_quotes(watchlist: set[str]) -> dict[str, dict]:
    """Build a `SchwabLosersUniverse` and pull its quotes. Raises on any
    failure (missing/invalid token file, network error, ...) — callers must
    catch and fall back to yfinance-only. Schwab auth is loaded lazily (on
    first network call, not at construction), so a missing token file
    (e.g. no `.cache/schwab_token.json`) surfaces here as a
    `SchwabAuthExpired` from inside `get_symbols()`."""
    settings = get_settings()
    auth = SchwabAuth(
        app_key=settings.schwab_app_key,
        app_secret=settings.schwab_app_secret,
        callback_url=settings.schwab_callback_url,
        token_path=settings.schwab_token_path,
    )
    client = SchwabClient(auth)
    universe = SchwabLosersUniverse(client=client, watchlist=watchlist)
    await universe.get_symbols()
    return await universe.get_quotes()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/quotes", response_model=list[QuoteOut])
async def get_quotes(symbols: str = Query(..., description="Comma-separated tickers")) -> list[QuoteOut]:
    tickers = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    infos = await asyncio.gather(*(_fetch_quote(t) for t in tickers))

    results: list[QuoteOut] = []
    for ticker, info in zip(tickers, infos):
        if info is None:
            continue
        results.append(
            QuoteOut(
                ticker=ticker,
                price=info.get("regularMarketPrice") or info.get("currentPrice"),
                change_pct=info.get("regularMarketChangePercent"),
                previous_close=info.get("regularMarketPreviousClose") or info.get("previousClose"),
                currency=info.get("currency"),
            )
        )
    return results


@app.get("/losers", response_model=list[LoserOut])
async def get_losers(limit: int = Query(_DEFAULT_LOSERS_LIMIT, ge=1, le=100)) -> list[LoserOut]:
    settings = get_settings()
    try:
        watchlist = load_watchlist(settings.watchlist_path)
    except Exception as exc:
        logger.warning("watchlist_load_error", error=str(exc))
        watchlist = set()

    yfinance_quotes = await _fetch_yfinance_losers_quotes(watchlist)

    try:
        schwab_quotes = await _fetch_schwab_losers_quotes(watchlist)
    except Exception as exc:
        # Catches SchwabAuthExpired (no/invalid `.cache/schwab_token.json`)
        # as well as any other Schwab-side failure (network, rate limit,
        # ...) — the /losers endpoint must degrade to yfinance-only rather
        # than ever 500ing on a Schwab problem.
        logger.warning("schwab_losers_unavailable", error=str(exc))
        schwab_quotes = {}

    ranked = merge_losers_quotes(yfinance_quotes, schwab_quotes, limit)

    prices = await asyncio.gather(*(_fetch_quote(ticker) for ticker, _ in ranked))
    results: list[LoserOut] = []
    for (ticker, meta), info in zip(ranked, prices):
        price = None
        if info is not None:
            price = info.get("regularMarketPrice") or info.get("currentPrice")
        sector = meta.get("sector") or (info.get("sector") if info else None)
        results.append(
            LoserOut(
                ticker=ticker,
                price=price,
                change_pct=meta.get("change_pct"),
                market_cap=meta.get("market_cap"),
                sector=sector,
            )
        )
    return results
