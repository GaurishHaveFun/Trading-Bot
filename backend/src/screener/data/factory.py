"""Provider factory + fallback wiring (Milestone 4 of the Schwab integration
plan). Composes the Schwab-backed providers (bars, losers-universe) with
their yfinance equivalents behind the existing DataProvider / UniverseProvider
interfaces, with automatic PER-CALL fallback to yfinance on any Schwab
failure — a transient Schwab failure for one symbol/call must not take down
an entire run. Fundamentals intentionally do NOT get this fallback treatment
(see build_fundamentals_provider's docstring)."""
from __future__ import annotations

from pathlib import Path

from screener.config import Settings
from screener.data.base import DataProvider
from screener.data.cache import BarCache
from screener.data.fundamentals_cache import FundamentalsCache
from screener.data.fundamentals_provider import FundamentalsProvider
from screener.data.schwab.auth import SchwabAuth
from screener.data.schwab.bars_provider import SchwabProvider
from screener.data.schwab.client import SchwabClient
from screener.data.schwab.universe import SchwabLosersUniverse
from screener.data.yfinance_provider import YFinanceProvider
from screener.universe.base import UniverseProvider
from screener.universe.losers import LosersUniverse
from screener.universe.static import StaticUniverse
from screener.utils.logging import get_logger

logger = get_logger(__name__)

_UNIVERSE_PATH = Path("config/universe.yaml")


def _build_schwab_auth(settings: Settings) -> SchwabAuth:
    return SchwabAuth(
        app_key=settings.schwab_app_key,
        app_secret=settings.schwab_app_secret,
        callback_url=settings.schwab_callback_url,
        token_path=settings.schwab_token_path,
    )


class _FallbackBarProvider(DataProvider):
    """Tries the Schwab bar provider first; on ANY exception (SchwabAuthExpired,
    SchwabAPIError, or anything else), logs a structured `provider_fallback`
    event and falls back to the yfinance provider for that same call. This
    happens per-call, not as a one-time startup decision."""

    def __init__(self, schwab: DataProvider, yfinance: DataProvider) -> None:
        self._schwab = schwab
        self._yfinance = yfinance

    async def get_bars(self, symbol, start, end, interval="1d"):
        try:
            return await self._schwab.get_bars(symbol, start, end, interval)
        except Exception as exc:
            logger.warning(
                "provider_fallback",
                symbol=symbol,
                provider="schwab",
                operation="get_bars",
                reason=str(exc),
            )
            return await self._yfinance.get_bars(symbol, start, end, interval)


def build_bar_provider(settings: Settings, cache: BarCache) -> DataProvider:
    """Returns a plain YFinanceProvider unless settings.data_provider ==
    'schwab', in which case it returns a schwab-first, yfinance-fallback
    wrapper. No behavior change from today when data_provider != 'schwab'."""
    if settings.data_provider != "schwab":
        return YFinanceProvider(cache)
    auth = _build_schwab_auth(settings)
    client = SchwabClient(auth)
    schwab_provider = SchwabProvider(client, cache)
    yfinance_provider = YFinanceProvider(cache)
    return _FallbackBarProvider(schwab_provider, yfinance_provider)


class _FallbackLosersUniverse(UniverseProvider):
    """Schwab-first, yfinance-fallback wrapper for the 'losers' universe.
    Tracks which underlying provider actually succeeded on the most recent
    get_symbols() call (`_active`) so get_quotes() routes to that SAME
    provider's stashed quotes rather than stale/empty state from the other
    one. quote_for() independently tries schwab-then-yfinance per call (it
    doesn't depend on get_symbols() having been called first)."""

    def __init__(self, schwab: SchwabLosersUniverse, yfinance: LosersUniverse) -> None:
        self._schwab = schwab
        self._yfinance = yfinance
        self._active: str | None = None  # "schwab" | "yfinance" | None

    async def get_symbols(self) -> list[str]:
        try:
            symbols = await self._schwab.get_symbols()
        except Exception as exc:
            logger.warning(
                "provider_fallback",
                provider="schwab",
                operation="get_symbols",
                reason=str(exc),
            )
            symbols = await self._yfinance.get_symbols()
            self._active = "yfinance"
            return symbols
        if symbols:
            self._active = "schwab"
            return symbols
        logger.warning(
            "provider_fallback",
            provider="schwab",
            operation="get_symbols",
            reason="schwab returned no data",
        )
        symbols = await self._yfinance.get_symbols()
        self._active = "yfinance"
        return symbols

    async def get_quotes(self) -> dict[str, dict]:
        if self._active == "schwab":
            try:
                quotes = await self._schwab.get_quotes()
            except Exception as exc:
                logger.warning(
                    "provider_fallback",
                    provider="schwab",
                    operation="get_quotes",
                    reason=str(exc),
                )
                quotes = await self._yfinance.get_quotes()
                self._active = "yfinance"
                return quotes
            if quotes:
                return quotes
            logger.warning(
                "provider_fallback",
                provider="schwab",
                operation="get_quotes",
                reason="schwab returned no data",
            )
            quotes = await self._yfinance.get_quotes()
            self._active = "yfinance"
            return quotes
        if self._active == "yfinance":
            return await self._yfinance.get_quotes()
        return {}

    async def quote_for(self, symbol: str) -> dict | None:
        try:
            quote = await self._schwab.quote_for(symbol)
        except Exception as exc:
            logger.warning(
                "provider_fallback",
                symbol=symbol,
                provider="schwab",
                operation="quote_for",
                reason=str(exc),
            )
            return await self._yfinance.quote_for(symbol)
        if quote is not None:
            return quote
        logger.warning(
            "provider_fallback",
            symbol=symbol,
            provider="schwab",
            operation="quote_for",
            reason="schwab returned no data",
        )
        return await self._yfinance.quote_for(symbol)


def _build_losers_universe(settings: Settings, watchlist: set[str]) -> UniverseProvider:
    """Losers-shaped universe provider: schwab-with-fallback when
    settings.data_provider == 'schwab', else the plain yfinance
    LosersUniverse (no behavior change from today in that case)."""
    if settings.data_provider != "schwab":
        return LosersUniverse(watchlist=watchlist)
    auth = _build_schwab_auth(settings)
    client = SchwabClient(auth)
    schwab_universe = SchwabLosersUniverse(client=client, watchlist=watchlist)
    yfinance_universe = LosersUniverse(watchlist=watchlist)
    return _FallbackLosersUniverse(schwab_universe, yfinance_universe)


def build_universe_provider(settings: Settings, watchlist: set[str]) -> UniverseProvider:
    """Composes BOTH config axes: settings.universe ('static' | 'losers')
    and settings.data_provider ('yfinance' | 'schwab'). StaticUniverse is
    unaffected by data_provider (it never fetches quotes over the network)."""
    if settings.universe == "losers":
        return _build_losers_universe(settings, watchlist)
    return StaticUniverse(_UNIVERSE_PATH)


def build_quote_lookup_provider(settings: Settings, watchlist: set[str]) -> UniverseProvider:
    """Always a losers-shaped provider (schwab-with-fallback-or-yfinance),
    regardless of settings.universe. Used by --ticker debug mode's single-
    symbol quote_for lookup, matching main.py's pre-existing behavior of
    always instantiating a LosersUniverse for this purpose even when
    settings.universe == 'static' (ticker-debug wants real quote metadata
    for the one symbol being debugged, independent of which universe mode
    is configured for full runs)."""
    return _build_losers_universe(settings, watchlist)


def build_fundamentals_provider(settings: Settings, cache: FundamentalsCache) -> FundamentalsProvider:
    """Decision 2 (locked, do not change without explicit sign-off): ALWAYS
    returns the yfinance-backed FundamentalsProvider, regardless of
    settings.data_provider. SchwabFundamentalsProvider (Milestone 3) never
    raises — Schwab's quote endpoint is a point-in-time snapshot, not the
    multi-year statements the quality gate's 6 metrics need, so it always
    returns an empty (years_available=0, all-metrics-None) snapshot rather
    than an exception. A naive try/except fallback ("try Schwab, catch
    exception, fall back to yfinance") would therefore NEVER actually fall
    back for fundamentals: it would silently accept Schwab's useless empty
    snapshot every time, and the quality gate (which needs real metric
    values to pass tickers through) would end up excluding every ticker.
    So fundamentals intentionally bypass the data_provider knob entirely —
    do not wire SchwabFundamentalsProvider in here without first solving
    that problem (a future revision, not this one)."""
    return FundamentalsProvider(cache)
