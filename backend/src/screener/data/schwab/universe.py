"""Schwab Trader API universe provider — quotes/movers-based "losers" screen
(Milestone 3).

Mirrors `screener.universe.losers.LosersUniverse`'s public shape
(`get_symbols`, `get_quotes`, `quote_for`) but sources data from Schwab's
movers + quotes endpoints via `SchwabClient` instead of yfinance.

Deliberate sync/async divergence from `UniverseProvider` (flagged, not
fixed here)
-------------------------------------------------------------------------
`screener.universe.base.UniverseProvider` declares `get_symbols`/`get_quotes`
as plain **sync** methods. This class implements `get_symbols`, `get_quotes`,
and `quote_for` as **`async def`** instead, because the only way to fetch
Schwab data is `SchwabClient.get`, which is itself `async def` (it awaits an
async rate limiter and an async `httpx` call) — there is no synchronous
Schwab HTTP path to call into.

Bridging sync-over-async here (e.g. wrapping the async calls in
`asyncio.run(...)` inside a nominally-sync `get_symbols`) would not be safe:
the current (and only) call site, `main.py`'s `run_screener()`, is itself
`async def` and calls `universe.get_symbols()` / `universe.get_quotes()`
**synchronously from inside that already-running event loop**, before any
`await`. Calling `asyncio.run(...)` from inside a running loop raises
`RuntimeError: asyncio.run() cannot be called from a running event loop` —
so a sync-bridge wrapper would blow up the instant this class is actually
wired in, rather than degrading gracefully.

This class therefore intentionally diverges from the ABC's sync signatures.
Python's `abc` module does not enforce sync/async at runtime, so subclassing
`UniverseProvider` this way does not raise, but a static type checker would
flag the signature mismatch (`async def` overriding a plain `def` in the
base) — that is expected and accepted for now, not a bug.

Reconciling this divergence is explicitly **out of scope** for this file —
it is Milestone 4's job (provider factory / fallback wiring, not part of
this milestone) to decide how the call site should handle it, e.g. by
`await`-ing these methods conditionally based on provider type, or by
introducing an async-capable base class/protocol that both `LosersUniverse`
(sync, yfinance) and `SchwabLosersUniverse` (async, Schwab) can satisfy.
"""
from __future__ import annotations

from screener.data.schwab.client import SchwabClient
from screener.universe.base import UniverseProvider
from screener.utils.logging import get_logger

logger = get_logger(__name__)

_MIN_MARKET_CAP = 10_000_000_000  # $10B cap floor — drop penny/small-cap losers
_TOP_N = 15

_MOVERS_PATH = "/marketdata/v1/movers/$SPX"
_QUOTES_PATH = "/marketdata/v1/quotes"


class SchwabLosersUniverse(UniverseProvider):
    """Screens Schwab's daily movers-down index and unions the result with
    any watchlist symbols that are also down today. See the module
    docstring for why `get_symbols`/`get_quotes`/`quote_for` are `async def`
    despite `UniverseProvider` declaring them sync."""

    def __init__(self, client: SchwabClient, watchlist: set[str] | None = None) -> None:
        self._client = client
        self._watchlist = watchlist or set()
        self._quotes: dict[str, dict] = {}

    async def get_symbols(self) -> list[str]:
        """Top-15 (by % loss) large-cap equity losers, unioned with
        watchlist symbols currently down today. Also stashes quote metadata
        for `get_quotes()`, fetched once per call."""
        movers = await self._fetch_movers()
        mover_symbols = [m.get("symbol") for m in movers if m.get("symbol")]

        quotes_by_symbol = await self._fetch_quotes_batch(mover_symbols)
        top = self._filter_and_rank(list(quotes_by_symbol.values()))
        watchlist_down = await self._watchlist_down_today()

        quotes: dict[str, dict] = {}
        for c in top:
            symbol = c.get("symbol")
            if symbol:
                quotes[symbol] = self._to_meta(c)
        for symbol, q in watchlist_down.items():
            quotes.setdefault(symbol, self._to_meta(q))

        self._quotes = quotes
        return sorted(quotes.keys())

    async def get_quotes(self) -> dict[str, dict]:
        """Per-symbol metadata (price_to_book, change_pct, market_cap,
        industry, sector) for the symbols returned by the most recent
        `get_symbols()` call."""
        return dict(self._quotes)

    async def quote_for(self, symbol: str) -> dict | None:
        """Fetch and shape quote metadata for a single arbitrary symbol
        (used by `--ticker` debug mode, which may target a name outside the
        current day's movers screen)."""
        info = await self._fetch_quote(symbol)
        if info is None:
            return None
        return self._to_meta(info)

    async def _fetch_movers(self) -> list[dict]:
        """Call Schwab's movers-down endpoint and return the raw screener
        list.

        # NOTE: the exact response shape assumed below — a dict with a
        # top-level `screeners` list, each item roughly
        # `{"symbol": ..., "description": ..., "volume": ..., "lastPrice":
        # ..., "netChange": ..., "netPercentChange": ..., "marketShare":
        # ...}` — is a best-effort, UNVERIFIED assumption based on publicly
        # documented conventions for Schwab's Trader API movers endpoint. It
        # has not been exercised against a real response yet (no live
        # credentials this session) and should be corrected against the
        # actual API docs/responses once available.
        """
        params = {"sort": "PERCENT_CHANGE_DOWN", "frequency": 0}
        try:
            payload = await self._client.get(_MOVERS_PATH, params)
        except Exception as exc:
            logger.warning("schwab_movers_error", error=str(exc))
            return []
        if not isinstance(payload, dict):
            return []
        return payload.get("screeners", []) or []

    def _filter_and_rank(self, candidates: list[dict]) -> list[dict]:
        """Drop non-EQUITY and sub-$10B market cap names, then take the top
        15 by % loss (most negative `netPercentChange` first).

        Note: Schwab's movers payload itself (see `_fetch_movers`'s NOTE)
        has no documented market-cap/quote-type field, so — unlike
        `LosersUniverse`, which filters directly on the single yfinance
        `day_losers` screen response — this filters on the flattened
        *quotes* response instead (`quoteType`/`marketCap`, sourced from the
        quotes endpoint's `reference`/`fundamental` sections; see
        `_flatten_quote_entry`), which is fetched for every mover symbol in
        `get_symbols` before this is called."""
        filtered = [
            c
            for c in candidates
            if c.get("quoteType") == "EQUITY" and (c.get("marketCap") or 0) >= _MIN_MARKET_CAP
        ]
        filtered.sort(key=lambda c: c.get("netPercentChange") or 0.0)
        return filtered[:_TOP_N]

    async def _watchlist_down_today(self) -> dict[str, dict]:
        """Fetch quotes for watchlist symbols and keep only those down today."""
        down: dict[str, dict] = {}
        for symbol in sorted(self._watchlist):
            info = await self._fetch_quote(symbol)
            if info is None:
                continue
            change_pct = info.get("netPercentChange")
            if change_pct is not None and change_pct < 0:
                down[symbol] = info
        return down

    async def _fetch_quote(self, symbol: str) -> dict | None:
        """Fetch+shape a single ticker's flattened quote dict. Isolated for
        easy mocking/testing and reuse by `quote_for`/`_watchlist_down_today`."""
        quotes = await self._fetch_quotes_batch([symbol])
        return quotes.get(symbol)

    async def _fetch_quotes_batch(self, symbols: list[str]) -> dict[str, dict]:
        """Call Schwab's quotes endpoint for one or more symbols and return
        a dict of flattened quote dicts keyed by symbol. Any failure
        (exception, non-dict payload) is caught/logged and yields an empty
        dict rather than raising.

        # NOTE (originally UNVERIFIED, now CONFIRMED against the real Schwab
        # OpenAPI spec pasted in from developer.schwab.com): the response is
        # a dict keyed by symbol -> QuoteResponseObject. For equities
        # (EquityResponse) the top-level `assetMainType`/`assetSubType`/
        # `symbol`/`quoteType` fields plus the nested `quote` (QuoteEquity),
        # `fundamental` (Fundamental/FundamentalInst), and `reference`
        # (ReferenceEquity) sections are CONFIRMED CORRECT — this nesting is
        # exactly what `_flatten_quote_entry` below reads. Within those
        # sections, `fundamental.pbRatio`, `fundamental.marketCap`, and
        # `entry.assetMainType` are CONFIRMED CORRECT field names.
        # CONFIRMED BUG (now fixed): `quote.netPercentChangeInDouble` does
        # NOT exist on QuoteEquity — the real field is `quote.netPercentChange`
        # (see `_flatten_quote_entry` below, which now reads the correct key).
        # CONFIRMED ABSENT: `reference.industry` / `reference.sector` are NOT
        # part of the real ReferenceEquity schema (confirmed fields are
        # cusip/description/exchange/exchangeName/fsiDesc/htbQuantity/
        # htbRate/isHardToBorrow/isShortable/otcMarketTier) — so
        # `_flatten_quote_entry`'s `reference.get("industry")`/
        # `reference.get("sector")` below will always resolve to `None`
        # against real Schwab data. Industry/sector enrichment for
        # Schwab-sourced quotes is handled separately, at the provider-
        # factory composition layer (see `screener.data.factory`'s
        # `_FallbackLosersUniverse` enrichment helper), not in this file.
        # Still genuinely UNVERIFIED: exact field availability/behavior
        # across all equity subtypes, and the Screener/movers response shape
        # (handled separately by `_fetch_movers` above, which is NOT touched
        # by this verification pass and keeps its own UNVERIFIED note).
        """
        if not symbols:
            return {}
        params = {"symbols": ",".join(symbols), "fields": "fundamental,quote,reference"}
        try:
            payload = await self._client.get(_QUOTES_PATH, params)
        except Exception as exc:
            logger.warning("schwab_quote_error", symbols=symbols, error=str(exc))
            return {}
        if not isinstance(payload, dict):
            return {}

        result: dict[str, dict] = {}
        for symbol, entry in payload.items():
            if isinstance(entry, dict):
                result[symbol] = self._flatten_quote_entry(symbol, entry)
        return result

    @staticmethod
    def _flatten_quote_entry(symbol: str, entry: dict) -> dict:
        """Flatten a single Schwab quotes-response entry's nested
        `quote`/`fundamental`/`reference` sections into a flat internal
        shape, mirroring the flat dicts yfinance's `Ticker.info` /
        `day_losers` screen already hand to `LosersUniverse._to_meta`.
        Whenever a Schwab field for one of `_to_meta`'s keys doesn't have an
        honest/obvious source, the corresponding key here is `None` rather
        than a fabricated guess.

        The `quote`/`fundamental`/`reference` nesting and the `pbRatio`/
        `marketCap`/`assetMainType`/`netPercentChange` field names read below
        are CONFIRMED CORRECT against the real Schwab OpenAPI spec (see the
        NOTE on `_fetch_quotes_batch` above). `reference.industry` and
        `reference.sector` are CONFIRMED ABSENT from the real ReferenceEquity
        schema and will always be `None` here when sourced directly from
        Schwab — industry/sector enrichment for Schwab quotes happens at the
        factory composition layer, not in this file."""
        quote = entry.get("quote") or {}
        fundamental = entry.get("fundamental") or {}
        reference = entry.get("reference") or {}
        return {
            "symbol": symbol,
            "netPercentChange": quote.get("netPercentChange"),
            "priceToBook": fundamental.get("pbRatio"),
            "marketCap": fundamental.get("marketCap"),
            "quoteType": entry.get("assetMainType"),
            "industry": reference.get("industry"),
            "sector": reference.get("sector"),
        }

    @staticmethod
    def _to_meta(q: dict) -> dict:
        """Shape a flattened internal quote dict into the exact metadata
        keys consumed downstream by `main.py`'s `_build_snapshot` and
        `rules/functions.py`'s `build_symbol_table` — do not rename/add/
        remove keys."""
        return {
            "price_to_book": q.get("priceToBook"),
            "change_pct": q.get("netPercentChange"),
            "market_cap": q.get("marketCap"),
            "industry": q.get("industry"),
            "sector": q.get("sector"),
        }
