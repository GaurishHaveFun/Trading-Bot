"""Provider factory + fallback wiring (Milestone 4 of the Schwab integration
plan). Composes the Schwab-backed providers (bars, losers-universe) with
their yfinance equivalents behind the existing DataProvider / UniverseProvider
interfaces, with automatic PER-CALL fallback to yfinance on any Schwab
failure — a transient Schwab failure for one symbol/call must not take down
an entire run. Bars use a pure schwab-then-fallback composition
(`_FallbackBarProvider`). Fundamentals and the losers-universe both instead
MERGE Schwab with yfinance rather than picking one exclusively:
`build_fundamentals_provider` layers Schwab's 2 real TTM metrics on top of a
full yfinance snapshot per-field when available (see
`_MergedFundamentalsProvider`'s docstring), and `_build_losers_universe`'s
`_MergedLosersUniverse` unions Schwab's movers (capped at ~10 by Schwab's own
API) with yfinance's independent top-20 scan into one top-20 universe
whenever Schwab is healthy, falling back to yfinance-only only when Schwab
itself fails or returns nothing (see `_MergedLosersUniverse`'s docstring)."""
from __future__ import annotations

from pathlib import Path

import yfinance as yf

from screener.config import Settings
from screener.data.base import DataProvider
from screener.data.cache import BarCache
from screener.data.fundamentals_cache import FundamentalsCache
from screener.data.fundamentals_provider import FundamentalsProvider
from screener.data.schwab.auth import SchwabAuth
from screener.data.schwab.bars_provider import SchwabProvider
from screener.data.schwab.client import SchwabClient
from screener.data.schwab.fundamentals_provider import SchwabFundamentalsProvider
from screener.data.schwab.universe import SchwabLosersUniverse
from screener.data.yfinance_provider import YFinanceProvider
from screener.models import FundamentalsSnapshot
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


async def _enrich_industry_sector(meta: dict, symbol: str) -> dict:
    """Best-effort fill-in of `industry`/`sector` for a Schwab-sourced quote
    meta dict. Schwab's ReferenceEquity schema has been confirmed (against
    the real Schwab OpenAPI spec) to carry NO industry/sector fields at all
    (see `screener.data.schwab.universe`'s confirmation notes), so
    Schwab-derived quotes always have `industry`/`sector` as `None` — which
    would silently break `main.py`'s `is_chip` detection (it checks
    `"semiconductor" in industry.lower()`). This falls back to a yfinance
    lookup, reusing the exact same `yf.Ticker(symbol).info` ->
    `.get("industry") or .get("industryKey")` / `.get("sector")` pattern
    already used by `screener.universe.losers.LosersUniverse._to_meta`.

    Only fills in whatever field(s) are actually missing — never overwrites
    an already-present value. Never raises: any yfinance lookup failure
    (network error, bad symbol, etc.) is caught, logged, and the meta dict
    is returned as-is (best-effort enrichment must never take down the
    whole quote lookup)."""
    if meta.get("industry") and meta.get("sector"):
        return meta

    # asyncio.to_thread judgment call: this is a blocking yfinance call made
    # from an `async def` method. `LosersUniverse` (screener/universe/
    # losers.py) already makes this exact same kind of blocking yfinance
    # call from inside its own `async def` methods WITHOUT wrapping it in
    # `asyncio.to_thread` (a known, explicitly-scoped-out gap from the
    # milestone that made those methods async). We match that existing
    # precedent here for consistency with the rest of this codebase's
    # current state, rather than introducing to_thread only for this one
    # new call site.
    try:
        info = yf.Ticker(symbol).info
    except Exception as exc:
        logger.warning("industry_sector_enrichment_error", symbol=symbol, error=str(exc))
        return meta

    enriched = dict(meta)
    if not enriched.get("industry"):
        enriched["industry"] = info.get("industry") or info.get("industryKey")
    if not enriched.get("sector"):
        enriched["sector"] = info.get("sector")
    return enriched


_MERGED_TOP_N = 20  # matches LosersUniverse/SchwabLosersUniverse's own _TOP_N


class _MergedLosersUniverse(UniverseProvider):
    """Schwab-primary, yfinance-UNIONED wrapper for the 'losers' universe.

    Schwab's `get_movers()` endpoint itself only ever returns the top ~10
    movers for an index (a real Schwab API limitation) — so a true top-20
    universe cannot come from Schwab alone. Per explicit user request ("use
    both, I actually want top 20"), this class combines Schwab's movers with
    yfinance's independent top-20 scan into a single unioned universe on
    every call where Schwab is healthy, rather than treating yfinance as a
    pure emergency fallback (which is how the pre-merge `_FallbackLosersUniverse`
    this class replaces used to behave).

    get_symbols() behavior:
      - Schwab raises OR returns an empty symbol list: degrades to a pure
        yfinance-only top-20, logging `provider_fallback` — unchanged in
        spirit from the pre-merge fallback behavior (get_quotes() also
        re-fetches lazily from yfinance in this branch, not from stored
        state, matching the old contract).
      - Schwab returns ANY symbols (even just a few): ALSO always calls
        yfinance's get_symbols()/get_quotes() and unions the two
        `{symbol: meta}` quote dicts. For a symbol present in both sources,
        the Schwab-sourced entry wins (Schwab is treated as primary
        elsewhere in this file, e.g. fundamentals merging) — but Schwab
        entries are still run through `_enrich_industry_sector` first
        (Schwab's schema has no industry/sector field at all; yfinance
        entries already carry real values and are never re-enriched). The
        unioned dict is then re-ranked by `change_pct` ascending (most
        negative/worst loss first, matching both sources' own internal
        `_filter_and_rank` sort direction) and sliced to the top 20. That
        top-20 dict is stored and is what get_quotes() returns.

    quote_for() is UNCHANGED from the old schwab-try-then-yfinance-fallback
    per-call behavior — it's a different codepath (used by `--ticker` debug
    mode for a symbol that may not even be in the current movers list) and
    is intentionally NOT part of the union/merge semantics above."""

    def __init__(self, schwab: SchwabLosersUniverse, yfinance: LosersUniverse) -> None:
        self._schwab = schwab
        self._yfinance = yfinance
        self._active: str | None = None  # "merged" | "yfinance" | None
        self._quotes: dict[str, dict] = {}

    async def get_symbols(self) -> list[str]:
        try:
            schwab_symbols = await self._schwab.get_symbols()
        except Exception as exc:
            logger.warning(
                "provider_fallback",
                provider="schwab",
                operation="get_symbols",
                reason=str(exc),
            )
            self._active = "yfinance"
            return await self._yfinance.get_symbols()

        if not schwab_symbols:
            logger.warning(
                "provider_fallback",
                provider="schwab",
                operation="get_symbols",
                reason="schwab returned no data",
            )
            self._active = "yfinance"
            return await self._yfinance.get_symbols()

        # Schwab succeeded (even partially) — ALWAYS also pull yfinance and
        # union the two sources; Schwab alone can never cover a top-20 (its
        # movers endpoint caps out around 10).
        schwab_quotes = await self._schwab.get_quotes()
        enriched_schwab = {
            symbol: await _enrich_industry_sector(meta, symbol)
            for symbol, meta in schwab_quotes.items()
        }

        await self._yfinance.get_symbols()
        yfinance_quotes = await self._yfinance.get_quotes()

        merged = dict(yfinance_quotes)
        merged.update(enriched_schwab)  # Schwab wins on symbol overlap

        ranked = sorted(merged.items(), key=lambda kv: kv[1].get("change_pct") or 0.0)
        self._quotes = dict(ranked[:_MERGED_TOP_N])
        self._active = "merged"
        return sorted(self._quotes.keys())

    async def get_quotes(self) -> dict[str, dict]:
        if self._active == "merged":
            return dict(self._quotes)
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
            # Same enrichment as get_symbols()'s merge above: only for the
            # Schwab-sourced result, never for the yfinance-fallback branch
            # below (which already has real industry/sector).
            return await _enrich_industry_sector(quote, symbol)
        logger.warning(
            "provider_fallback",
            symbol=symbol,
            provider="schwab",
            operation="quote_for",
            reason="schwab returned no data",
        )
        return await self._yfinance.quote_for(symbol)


def _build_losers_universe(settings: Settings, watchlist: set[str]) -> UniverseProvider:
    """Losers-shaped universe provider: schwab-primary-yfinance-unioned when
    settings.data_provider == 'schwab', else the plain yfinance
    LosersUniverse (no behavior change from today in that case)."""
    if settings.data_provider != "schwab":
        return LosersUniverse(watchlist=watchlist)
    auth = _build_schwab_auth(settings)
    client = SchwabClient(auth)
    schwab_universe = SchwabLosersUniverse(client=client, watchlist=watchlist)
    yfinance_universe = LosersUniverse(watchlist=watchlist)
    return _MergedLosersUniverse(schwab_universe, yfinance_universe)


def build_universe_provider(settings: Settings, watchlist: set[str]) -> UniverseProvider:
    """Composes BOTH config axes: settings.universe ('static' | 'losers')
    and settings.data_provider ('yfinance' | 'schwab'). StaticUniverse is
    unaffected by data_provider (it never fetches quotes over the network)."""
    if settings.universe == "losers":
        return _build_losers_universe(settings, watchlist)
    return StaticUniverse(_UNIVERSE_PATH)


def build_quote_lookup_provider(settings: Settings, watchlist: set[str]) -> UniverseProvider:
    """Always a losers-shaped provider (schwab-primary-yfinance-unioned, or
    plain yfinance when data_provider != 'schwab'), regardless of
    settings.universe. Used by --ticker debug mode's single-symbol
    quote_for lookup, matching main.py's pre-existing behavior of always
    instantiating a LosersUniverse for this purpose even when
    settings.universe == 'static' (ticker-debug wants real quote metadata
    for the one symbol being debugged, independent of which universe mode
    is configured for full runs)."""
    return _build_losers_universe(settings, watchlist)


class _NoOpFundamentalsCache:
    """Stand-in for `FundamentalsCache` used ONLY for the internal
    `SchwabFundamentalsProvider` instance inside `_MergedFundamentalsProvider`.

    `SchwabFundamentalsProvider` writes whatever snapshot it builds into
    whatever cache it's constructed with, keyed only by symbol
    (`cache.put(symbol, snapshot)`). If it were handed the SAME shared
    `FundamentalsCache` the yfinance provider uses, its partial (3-of-6-
    metrics, years_available=0) snapshot could silently clobber a
    previously-cached, correctly-merged snapshot on a later cache read (or
    vice versa) — the cache table has no concept of "this row is a partial
    Schwab snapshot" vs. "this row is the final merged snapshot" for the
    same symbol key. Handing Schwab's own provider this no-op cache instead
    means its `get_fundamentals()` always does a fresh fetch (fine — it's
    just one lightweight quote call) and never writes into the shared cache
    table. The merged snapshot itself is never cached by
    `_MergedFundamentalsProvider` either, but that's fine: the yfinance
    provider's own call already caches its half of the data normally via the
    shared `FundamentalsCache`, and Schwab's 3 fields are cheap enough to
    refetch on every call."""

    def get(self, symbol: str) -> FundamentalsSnapshot | None:
        return None

    def put(self, symbol: str, snapshot: FundamentalsSnapshot) -> None:
        return None


class _MergedFundamentalsProvider:
    """Schwab-augmented fundamentals: yfinance is the base/fallback source of
    truth for everything (`fcf_5y_cumulative`, `ocf_ni_ratio`,
    `share_dilution_5y`, `years_available`, `ticker`, `as_of`, and now also
    `interest_coverage`), while `gross_margin`/`net_margin` are taken from
    Schwab's real TTM quote fields whenever Schwab succeeds AND that
    specific field is not `None` — per-field independently, falling back to
    yfinance's value for any field Schwab didn't provide. Schwab failing
    entirely (any exception) degrades gracefully to a snapshot identical to
    plain yfinance fundamentals.

    `interest_coverage` is deliberately excluded from `_SCHWAB_FIELDS`: live
    testing found Schwab's `interestCoverage` returned exactly `0.0` for
    both AAPL and MSFT, which isn't plausible for either company and looks
    like a systematic Schwab data artifact rather than real data. Because
    the merge rule below only checks `value is not None`, a bogus-but-non-
    None `0.0` would otherwise silently override yfinance's real value —
    and since the quality gate's `min_interest_coverage` threshold is well
    above zero, that would incorrectly fail tickers on this metric alone.
    yfinance's `interest_coverage` is used unconditionally instead."""

    _SCHWAB_FIELDS = ("gross_margin", "net_margin")

    def __init__(
        self, schwab: SchwabFundamentalsProvider, yfinance: FundamentalsProvider
    ) -> None:
        self._schwab = schwab
        self._yfinance = yfinance

    async def get_fundamentals(self, symbol: str) -> FundamentalsSnapshot | None:
        base = await self._yfinance.get_fundamentals(symbol)
        if base is None:
            return None

        try:
            schwab_snapshot = await self._schwab.get_fundamentals(symbol)
        except Exception as exc:
            logger.warning("schwab_fundamentals_error", symbol=symbol, error=str(exc))
            schwab_snapshot = None

        update: dict[str, float | None] = {}
        if schwab_snapshot is not None:
            for field in self._SCHWAB_FIELDS:
                value = getattr(schwab_snapshot, field)
                if value is not None:
                    update[field] = value

        if not update:
            return base

        logger.info("schwab_fundamentals_merged", symbol=symbol, fields=list(update.keys()))
        return base.model_copy(update=update)


def build_fundamentals_provider(
    settings: Settings, cache: FundamentalsCache
) -> FundamentalsProvider | _MergedFundamentalsProvider:
    """Revised design (supersedes the earlier "always plain yfinance"
    decision, now that the real Schwab OpenAPI schema confirms Schwab's
    `fundamental` section carries real TTM values for 2 of the 6
    quality-gate metrics — gross_margin/net_margin — via
    `grossMarginTTM`/`netProfitMarginTTM`). Schwab's `fundamental` section
    does also return an `interestCoverage` value, but it's intentionally
    excluded from the merge — see `_MergedFundamentalsProvider`'s docstring
    for why (Schwab returned exactly `0.0` for both AAPL and MSFT in live
    testing).

    - settings.data_provider != 'schwab': unchanged, returns the plain
      yfinance-backed `FundamentalsProvider`.
    - settings.data_provider == 'schwab': returns a `_MergedFundamentalsProvider`
      that layers Schwab's 2 real TTM fields on top of a full yfinance
      snapshot (which remains the source of truth for the other 4 metrics,
      including interest_coverage, and for years_available). The internal
      `SchwabFundamentalsProvider` is built with a private
      `_NoOpFundamentalsCache` (see that class's docstring) rather than the
      shared `cache`, so its partial snapshot never clobbers the shared
      cache table."""
    if settings.data_provider != "schwab":
        return FundamentalsProvider(cache)
    auth = _build_schwab_auth(settings)
    client = SchwabClient(auth)
    schwab_fundamentals = SchwabFundamentalsProvider(client, _NoOpFundamentalsCache())
    yfinance_fundamentals = FundamentalsProvider(cache)
    return _MergedFundamentalsProvider(schwab_fundamentals, yfinance_fundamentals)
