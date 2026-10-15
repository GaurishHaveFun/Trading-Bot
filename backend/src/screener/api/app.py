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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from screener.analysis import cache as analysis_cache
from screener.analysis import context as analysis_context
from screener.analysis import engine as analysis_engine
from screener.analysis.models import CachedAnalysis, TickerAnalysis
from screener.api.schwab_quotes import fetch_schwab_quotes as _fetch_schwab_quotes_raw
from screener.api.schwab_quotes import static_sector
from screener.api.ticker_data import (
    TickerProfileOut,
    TickerStatsOut,
    TickerTargetsOut,
    fetch_quote_info as _fetch_quote_info,
    looks_like_real_symbol as _looks_like_real_symbol,
    num as _num,
    pick as _pick,
    to_profile as _to_profile,
    to_stats as _to_stats,
    to_targets as _to_targets,
)
from screener.config import get_settings, load_watchlist
from screener.data.base import DataProvider
from screener.data.cache import BarCache
from screener.data.factory import build_bar_provider
from screener.data.schwab.auth import SchwabAuth
from screener.data.schwab.client import SchwabClient
from screener.data.schwab.universe import SchwabLosersUniverse
from screener.models import Bar
from screener.universe.losers import LosersUniverse
from screener.utils.logging import get_logger

logger = get_logger(__name__)

_BAR_CACHE_PATH = Path(".cache/bars.db")
_DETAIL_BAR_DAYS = 400  # 52W of trading history plus margin

# Built lazily in `lifespan` (or by `_get_bar_provider` for tests that never
# trigger lifespan, e.g. `tests/test_api.py`'s module-level `TestClient(app)`
# with no `with` block). Not built at import time so `get_settings()` stays
# monkeypatchable, matching the reasoning in `lifespan`'s own docstring.
_bar_cache: BarCache | None = None
_bar_provider: DataProvider | None = None


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
    global _bar_cache, _bar_provider

    token_json = os.environ.get("SCHWAB_TOKEN_JSON", "").strip()
    if token_json:
        settings = get_settings()
        token_path = Path(settings.schwab_token_path)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(token_json)
        logger.info("schwab_token_materialized_from_env", token_path=str(token_path))
    else:
        logger.debug("schwab_token_env_not_set")

    _bar_cache = BarCache(_BAR_CACHE_PATH)
    _bar_provider = build_bar_provider(get_settings(), _bar_cache)

    yield

    if _bar_cache is not None:
        _bar_cache.close()
    _bar_cache = None
    _bar_provider = None


app = FastAPI(title="Trading Bot API", lifespan=lifespan)

_QUOTE_SEMAPHORE = asyncio.Semaphore(10)
_DEFAULT_LOSERS_LIMIT = 20

_ANALYSIS_SEMAPHORE = asyncio.Semaphore(2)
_analysis_locks: dict[str, asyncio.Lock] = {}
_ANALYSIS_RECHECK_MAX_AGE_MINUTES = 2


def _get_analysis_lock(ticker: str) -> asyncio.Lock:
    lock = _analysis_locks.get(ticker)
    if lock is None:
        lock = asyncio.Lock()
        _analysis_locks[ticker] = lock
    return lock


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


class BarOut(BaseModel):
    ticker: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


class TickerDetailOut(BaseModel):
    ticker: str
    profile: TickerProfileOut
    stats: TickerStatsOut
    targets: TickerTargetsOut
    bars: list[BarOut]


class TickerAnalysisOut(BaseModel):
    status: Literal["ready", "none"]
    generated_at: datetime | None
    age_minutes: int | None
    model: str | None
    analysis: TickerAnalysis | None


def _get_bar_provider() -> DataProvider:
    """Lazily builds `_bar_provider` if `lifespan` never ran (e.g.
    `tests/test_api.py`'s module-level `TestClient(app)` with no `with`
    block)."""
    global _bar_cache, _bar_provider
    if _bar_provider is None:
        if _bar_cache is None:
            _bar_cache = BarCache(_BAR_CACHE_PATH)
        _bar_provider = build_bar_provider(get_settings(), _bar_cache)
    return _bar_provider


async def _fetch_bars(symbol: str, days: int = _DETAIL_BAR_DAYS) -> list[Bar]:
    """Semaphore-bounded fetch of recent daily bars for the ticker detail
    page. Never raises -- any failure is logged as `detail_bars_error` and
    an empty list is returned, since a bars outage must never 500 the
    detail page."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    provider = _get_bar_provider()
    async with _QUOTE_SEMAPHORE:
        try:
            return await provider.get_bars(symbol, start, end)
        except Exception as exc:
            logger.warning("detail_bars_error", ticker=symbol, error=str(exc))
            return []


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


async def _fetch_schwab_quotes(symbols: list[str]) -> dict[str, dict]:
    """Primary quote source (works on Render). Never raises: any Schwab
    failure (SchwabAuthExpired, network, ...) is logged and yields {} so
    callers fall back to the yfinance path."""
    try:
        return await _fetch_schwab_quotes_raw(symbols)
    except Exception as exc:
        logger.warning("schwab_quotes_unavailable", error=str(exc), symbols=symbols)
        return {}


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


async def _schwab_or_yfinance(symbol: str, schwab: dict[str, dict]) -> dict | None:
    """Schwab quote if we have one, else the (best-effort) yfinance path."""
    info = schwab.get(symbol)
    if info:
        return info
    return await _fetch_quote(symbol)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/quotes", response_model=list[QuoteOut])
async def get_quotes(symbols: str = Query(..., description="Comma-separated tickers")) -> list[QuoteOut]:
    tickers = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    schwab = await _fetch_schwab_quotes(tickers)
    infos = await asyncio.gather(
        *(_schwab_or_yfinance(t, schwab) for t in tickers)
    )

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

    schwab_prices = await _fetch_schwab_quotes([t for t, _ in ranked])

    async def _price_info(ticker: str, meta: dict) -> dict | None:
        # yfinance is only needed when Schwab has no price, or when no
        # sector is known from the meta / static map.
        info = schwab_prices.get(ticker)
        if info and (meta.get("sector") or static_sector(ticker)):
            return info
        yf_info = await _fetch_quote(ticker)
        if info:
            return {**info, "sector": (yf_info or {}).get("sector")}
        return yf_info

    prices = await asyncio.gather(*(_price_info(t, m) for t, m in ranked))
    results: list[LoserOut] = []
    for (ticker, meta), info in zip(ranked, prices):
        price = None
        if info is not None:
            price = info.get("regularMarketPrice") or info.get("currentPrice")
        sector = (
            meta.get("sector")
            or static_sector(ticker)
            or (info.get("sector") if info else None)
        )
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


@app.get("/tickers/{symbol}", response_model=TickerDetailOut)
async def get_ticker_detail(symbol: str) -> TickerDetailOut:
    ticker = symbol.strip().upper()
    info, bars = await asyncio.gather(_fetch_quote(ticker), _fetch_bars(ticker))
    if info is None or not _looks_like_real_symbol(info):
        raise HTTPException(404, f"No data for symbol {ticker}")

    return TickerDetailOut(
        ticker=ticker,
        profile=_to_profile(info),
        stats=_to_stats(info),
        targets=_to_targets(info),
        bars=[
            BarOut(
                ticker=ticker,
                timestamp=bar.timestamp,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
            )
            for bar in bars
        ],
    )


def _to_analysis_out(cached: CachedAnalysis) -> TickerAnalysisOut:
    age = int((datetime.now(timezone.utc) - cached.generated_at).total_seconds() // 60)
    return TickerAnalysisOut(
        status="ready",
        generated_at=cached.generated_at,
        age_minutes=age,
        model=cached.model,
        analysis=cached.analysis,
    )


_NO_ANALYSIS_OUT = TickerAnalysisOut(status="none", generated_at=None, age_minutes=None, model=None, analysis=None)


@app.get("/tickers/{symbol}/analysis", response_model=TickerAnalysisOut)
async def get_ticker_analysis(
    symbol: str,
    max_age_minutes: int = Query(default=None, ge=1, le=10080),
) -> TickerAnalysisOut:
    ticker = symbol.strip().upper()
    settings = get_settings()
    effective_max_age = max_age_minutes if max_age_minutes is not None else settings.analysis_ttl_minutes
    cached = await analysis_cache.get_fresh(ticker, effective_max_age)
    if cached is None:
        return _NO_ANALYSIS_OUT
    return _to_analysis_out(cached)


@app.post("/tickers/{symbol}/analysis", response_model=TickerAnalysisOut)
async def post_ticker_analysis(symbol: str) -> TickerAnalysisOut:
    ticker = symbol.strip().upper()

    async with _get_analysis_lock(ticker):
        # Re-check with a short freshness window: if another waiter already
        # generated+cached an analysis for this ticker while we were
        # blocked on the lock, reuse it instead of calling analyze() again.
        cached = await analysis_cache.get_fresh(ticker, _ANALYSIS_RECHECK_MAX_AGE_MINUTES)
        if cached is not None:
            return _to_analysis_out(cached)

        async with _ANALYSIS_SEMAPHORE:
            evidence = await analysis_context.build_evidence(ticker)
            if evidence is None:
                raise HTTPException(404, f"No data for symbol {ticker}")

            try:
                result = await analysis_engine.analyze(evidence)
            except analysis_engine.AnalysisUnavailable as exc:
                logger.warning("analysis_request_failed", ticker=ticker, error_class=type(exc).__name__)
                raise HTTPException(503, "AI analysis is not configured (missing ANTHROPIC_API_KEY)") from exc
            except analysis_engine.AnalysisTimeout as exc:
                logger.warning("analysis_request_failed", ticker=ticker, error_class=type(exc).__name__)
                raise HTTPException(504, "AI analysis timed out") from exc
            except analysis_engine.AnalysisRateLimited as exc:
                logger.warning("analysis_request_failed", ticker=ticker, error_class=type(exc).__name__)
                raise HTTPException(429, "AI analysis is rate limited, try again shortly") from exc
            except (
                analysis_engine.AnalysisRefused,
                analysis_engine.AnalysisProviderError,
                analysis_engine.AnalysisParseError,
            ) as exc:
                logger.warning("analysis_request_failed", ticker=ticker, error_class=type(exc).__name__)
                raise HTTPException(502, "AI analysis failed") from exc

            cached = CachedAnalysis(
                ticker=ticker,
                generated_at=datetime.now(timezone.utc),
                model=get_settings().analysis_model,
                change_pct=evidence.technicals.get("change_pct_1d"),
                analysis=result,
            )
            await analysis_cache.put(cached)
            return _to_analysis_out(cached)
