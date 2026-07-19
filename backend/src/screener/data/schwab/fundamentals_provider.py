"""Schwab Trader API fundamentals provider (Milestone 3; field-population
update after real Schwab OpenAPI schema verification).

Schwab's Trader API quotes/fundamental fields are a single point-in-time
quote snapshot — not the multi-year annual financial statements this
screener's quality gate actually needs. Of the 6 quality-gate metrics
computed by `screener.data.fundamentals_provider.FundamentalsProvider` (the
yfinance provider) — `fcf_5y_cumulative`, `interest_coverage`,
`gross_margin`, `ocf_ni_ratio`, `net_margin`, `share_dilution_5y` — the real,
confirmed Schwab `Fundamental`/`FundamentalInst` schema DOES carry current/
TTM values for 3 of them:

  - `interest_coverage`  <- fundamental.interestCoverage
  - `gross_margin`       <- fundamental.grossMarginTTM
  - `net_margin`         <- fundamental.netProfitMarginTTM

These are now populated (see `get_fundamentals` below), each defensively
coerced — a missing/None/non-numeric value leaves that one field `None`
without affecting the other two or raising.

The remaining 3 metrics — `fcf_5y_cumulative`, `ocf_ni_ratio`,
`share_dilution_5y` — remain structurally impossible for Schwab to supply:
they require multi-year income-statement / cash-flow / balance-sheet
history, and Schwab's quote/fundamental payload carries only a single
current ratio or TTM/MRQ figure, never a historical series. These 3 stay
`None` here, always, regardless of what Schwab returns.

`years_available` stays `0` for THIS PROVIDER'S OWN snapshot, always — even
though 3 real fields are now populated from a live quote, this provider
still has zero years of *historical* data of its own; `years_available` is
meant as a proxy for "how much usable multi-year history backs this
snapshot," and claiming anything above `0` here would overstate that. The
merge layer (`screener.data.factory._MergedFundamentalsProvider`) is
responsible for using yfinance's `years_available` in the final merged
snapshot handed to the quality gate.

The `get_fundamentals` return-type annotation (`FundamentalsSnapshot |
None`) is kept only for interface parity with the mirrored yfinance
provider (`FundamentalsProvider.get_fundamentals`) — in practice this
method never returns bare `None`; a real snapshot (partially populated, 3-
of-6 metrics) is always produced and cached, matching that provider's
contract exactly.
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
        call at all), else fetch a live Schwab quote and build a snapshot
        with `interest_coverage`/`gross_margin`/`net_margin` populated from
        the quote's `fundamental` section (each defensively coerced —- see
        `_safe_float`), the other 3 metrics always `None`, and
        `years_available=0` (see module docstring for why). Caches and
        returns it. Always returns a real snapshot — never actually returns
        bare `None`."""
        cached = self._cache.get(symbol)
        if cached is not None:
            return cached

        entry = await self._fetch_quote(symbol)

        interest_coverage: float | None = None
        gross_margin: float | None = None
        net_margin: float | None = None

        if entry is not None:
            fundamental = entry.get("fundamental") or {}

            # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
            # !!! UNVERIFIED — VERIFY AGAINST FIRST LIVE RESPONSE !!!
            # It is NOT verified whether Schwab returns interestCoverage /
            # grossMarginTTM / netProfitMarginTTM as a plain ratio (e.g.
            # 0.22) or as a percentage (e.g. 22.0). We DEFAULT to treating
            # them as plain ratios (no /100 conversion) below, matching
            # yfinance's convention, since that is the more common REST
            # convention for ratio-shaped fields — but this MUST be checked
            # against the first real live API response once real
            # credentials are used. Getting this wrong would silently
            # corrupt the quality gate's threshold comparisons (e.g. a
            # margin of "22" being compared against a 0-1 threshold).
            # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
            interest_coverage = self._safe_float(symbol, "interest_coverage", fundamental.get("interestCoverage"))
            gross_margin = self._safe_float(symbol, "gross_margin", fundamental.get("grossMarginTTM"))
            net_margin = self._safe_float(symbol, "net_margin", fundamental.get("netProfitMarginTTM"))

        snapshot = FundamentalsSnapshot(
            ticker=symbol,
            as_of=datetime.now(timezone.utc),
            years_available=0,
            fcf_5y_cumulative=None,
            interest_coverage=interest_coverage,
            gross_margin=gross_margin,
            ocf_ni_ratio=None,
            net_margin=net_margin,
            share_dilution_5y=None,
        )
        self._cache.put(symbol, snapshot)
        return snapshot

    @staticmethod
    def _safe_float(symbol: str, metric_name: str, raw: object) -> float | None:
        """Defensively coerce a raw fundamental field to `float`. Missing
        (`None`), and non-numeric/malformed values yield `None` for that
        one field only (logged, not raised) — mirrors the per-field
        independence of the yfinance provider's `_safe_metric` (see
        `screener.data.fundamentals_provider`), so one bad field never
        blanks out the other two."""
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            logger.warning("fundamentals_metric_unavailable", symbol=symbol, metric=metric_name)
            return None

    async def _fetch_quote(self, symbol: str) -> dict | None:
        """Fetch a single symbol's Schwab quote entry, defensively. Wraps
        the HTTP call + payload parsing in a try/except so a malformed or
        missing Schwab response never crashes the caller — logs a warning
        and returns `None` instead.

        # NOTE (originally UNVERIFIED, now CONFIRMED against the real
        # Schwab OpenAPI spec pasted in from developer.schwab.com): the
        # response is a dict keyed by symbol, and the nested `quote`/
        # `fundamental`/`reference` sections ARE the correct nesting
        # (consistent with `screener.data.schwab.universe`'s confirmation of
        # the same shape). Confirmed real `fundamental` field names used
        # above include `interestCoverage`, `grossMarginTTM`,
        # `netProfitMarginTTM`, `pbRatio`, `marketCap`. Still genuinely
        # unverified: exact field availability/behavior across all equity
        # subtypes, and whether the 3 ratio fields above are plain ratios or
        # percentages (see the prominent warning in `get_fundamentals`).
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
