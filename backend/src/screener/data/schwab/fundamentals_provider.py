"""Schwab Trader API fundamentals provider (Milestone 3).

Schwab's Trader API quotes/fundamental fields are a single point-in-time
quote snapshot — not the multi-year annual financial statements this
screener's quality gate actually needs. All 6 quality-gate metrics computed
by `screener.data.fundamentals_provider.FundamentalsProvider` (the yfinance
provider) — `fcf_5y_cumulative`, `interest_coverage`, `gross_margin`,
`ocf_ni_ratio`, `net_margin`, `share_dilution_5y` — require multiple years
of income-statement / cash-flow / balance-sheet history that a single
Schwab quote simply does not carry.

Per the integration plan (Milestone 3): "Where Schwab lacks the data,
return `None` metrics (the quality gate already tolerates `None`) and let
the fallback layer prefer yfinance fundamentals — do NOT fabricate."

Accordingly, this provider ALWAYS returns all six metrics as `None` and
`years_available = 0` — it never computes or guesses a value from the
point-in-time quote. `years_available = 0` (rather than `1`) is the
deliberate choice here: although a live quote for the symbol is in fact
fetched, none of the 6 multi-year metrics are ever populated from it, and
`0` is the more honest signal to any downstream consumer that inspects
`years_available` as a proxy for "how much usable history backs this
snapshot" — claiming `1` would overstate what was actually derived from
the fetch.

The `get_fundamentals` return-type annotation (`FundamentalsSnapshot |
None`) is kept only for interface parity with the mirrored yfinance
provider (`FundamentalsProvider.get_fundamentals`) — in practice this
method never returns bare `None`; a real (all-`None`-metrics) snapshot is
always produced and cached, matching that provider's contract exactly.
"""
from __future__ import annotations

from datetime import datetime, timezone

from screener.data.fundamentals_cache import FundamentalsCache
from screener.data.schwab.client import SchwabClient
from screener.models import FundamentalsSnapshot
from screener.utils.logging import get_logger

logger = get_logger(__name__)

_QUOTES_PATH = "/marketdata/v1/quotes"


class SchwabFundamentalsProvider:
    def __init__(self, client: SchwabClient, cache: FundamentalsCache) -> None:
        self._client = client
        self._cache = cache

    async def get_fundamentals(self, symbol: str) -> FundamentalsSnapshot | None:
        """Return a FundamentalsSnapshot from cache (cache hit -> no network
        call at all), else fetch a live Schwab quote and build an
        all-`None`-metrics/`years_available=0` snapshot (see module
        docstring for why), cache it, and return it. Always returns a real
        snapshot — never actually returns bare `None`."""
        cached = self._cache.get(symbol)
        if cached is not None:
            return cached

        # Best-effort fetch: exercised for parity with the yfinance provider
        # and to keep a single, already-guarded place to start extracting
        # point-in-time fields from in a future revision. The result is
        # intentionally not used to populate any of the 6 metrics — see
        # module docstring.
        await self._fetch_quote(symbol)

        snapshot = self._empty_snapshot(symbol)
        self._cache.put(symbol, snapshot)
        return snapshot

    async def _fetch_quote(self, symbol: str) -> dict | None:
        """Fetch a single symbol's Schwab quote entry, defensively. Wraps
        the HTTP call + payload parsing in a try/except so a malformed or
        missing Schwab response never crashes the caller — logs a warning
        and returns `None` instead, mirroring the yfinance provider's
        `_safe_metric` defensive pattern even though nothing is computed
        from the result here.

        # NOTE: the exact response shape assumed below (a dict keyed by
        # symbol, with a nested `fundamental` section) is a best-effort,
        # UNVERIFIED assumption based on publicly documented conventions
        # for Schwab's Trader API quotes endpoint (exact nesting
        # unverified). It has not been exercised against a real response
        # yet (no live credentials this session) and should be corrected
        # against the actual API docs/responses once available.
        """
        params = {"symbols": symbol, "fields": "fundamental,quote,reference"}
        try:
            payload = await self._client.get(_QUOTES_PATH, params)
        except Exception as exc:
            logger.warning(
                "fundamentals_metric_unavailable",
                symbol=symbol,
                metric="schwab_fundamentals",
                error=str(exc),
            )
            return None

        if not isinstance(payload, dict):
            logger.warning(
                "fundamentals_metric_unavailable", symbol=symbol, metric="schwab_fundamentals"
            )
            return None

        entry = payload.get(symbol)
        if not isinstance(entry, dict):
            logger.warning(
                "fundamentals_metric_unavailable", symbol=symbol, metric="schwab_fundamentals"
            )
            return None

        return entry

    @staticmethod
    def _empty_snapshot(symbol: str) -> FundamentalsSnapshot:
        """All 6 quality-gate metrics are `None` and `years_available=0` —
        see module docstring for why this provider never fabricates
        multi-year metrics from a single Schwab quote."""
        return FundamentalsSnapshot(
            ticker=symbol,
            as_of=datetime.now(timezone.utc),
            years_available=0,
            fcf_5y_cumulative=None,
            interest_coverage=None,
            gross_margin=None,
            ocf_ni_ratio=None,
            net_margin=None,
            share_dilution_5y=None,
        )
