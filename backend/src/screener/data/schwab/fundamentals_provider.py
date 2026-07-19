"""Schwab Trader API fundamentals provider (Milestone 3; live-verified
against the real Schwab API this session — see the CONFIRMED notes below).

Schwab's Trader API has no multi-year annual financial statements endpoint —
this screener's quality gate needs 6 metrics
(`screener.data.fundamentals_provider.FundamentalsProvider`, the yfinance
provider, computes all 6): `fcf_5y_cumulative`, `interest_coverage`,
`gross_margin`, `ocf_ni_ratio`, `net_margin`, `share_dilution_5y`. Of those,
Schwab's single-snapshot `FundamentalInst` object carries current/TTM values
for exactly 3:

  - `interest_coverage`  <- fundamental.interestCoverage
  - `gross_margin`       <- fundamental.grossMarginTTM / 100
  - `net_margin`         <- fundamental.netProfitMarginTTM / 100

CONFIRMED VIA LIVE API CALL this session (real credentials, real responses
for AAPL and MSFT — not mocks): the correct endpoint for these fields is
**`/marketdata/v1/instruments?projection=fundamental`**
(`client.raw.get_instruments(symbols, projection=Instrument.Projection.FUNDAMENTAL)`),
**not** `/marketdata/v1/quotes`. The quotes endpoint's `fundamental` section
was live-checked and contains only dividend/volume/EPS/PE-ratio fields
(`avg10DaysVolume`, `divAmount`, `eps`, `peRatio`, `sharesOutstanding`, ...) —
none of `interestCoverage`/`grossMarginTTM`/`netProfitMarginTTM` are present
there at all, so the previous quotes-based implementation always silently
returned `None` for all 3 metrics (the merge layer masked this by falling
back to yfinance). The instruments/fundamental endpoint's real response
envelope is a top-level `"instruments"` **list** (not a dict keyed by
symbol, unlike `get_quotes`):

    {"instruments": [{"symbol": "AAPL", "fundamental": {...}, ...}]}

Since `get_fundamentals` fetches one symbol at a time, this provider matches
the list entry whose own `"symbol"` field equals the requested symbol
(falling back to `instruments[0]` if that lookup fails, since a single-symbol
request should only ever return zero or one entries).

CONFIRMED VIA LIVE API CALL (percentage-vs-ratio question, previously
UNVERIFIED and defaulted incorrectly): `grossMarginTTM`/`netProfitMarginTTM`
are returned as **percentages**, not 0-1 ratios. Live AAPL response:
`grossMarginTTM=47.8624`, `netProfitMarginTTM=27.1518` — these match AAPL's
real-world ~48%/~27% margins, confirming the percentage convention. Live
MSFT response: `grossMarginTTM=68.3092`, `netProfitMarginTTM=39.3423` —
consistent with MSFT's real ~68%/~39% margins, corroborating the same
convention on a second symbol. Both are therefore divided by 100 below
before being stored, to match the 0-1 ratio convention the yfinance provider
and `config/quality_screen.yaml` thresholds (e.g. `min_gross_margin: 0.15`)
already use.

CAVEAT — `interestCoverage` is NOT rescaled (it's a coverage ratio, e.g.
"5.2x", not a percentage — no /100 conversion applies) but is NOT fully
trusted either: **both live AAPL and live MSFT responses returned
`interestCoverage: 0.0`** this session, which does not look like a
plausible real value for either company (neither is a zero-interest-
coverage business). Seeing it on two unrelated large-caps suggests this may
be a systematically unpopulated/placeholder field on Schwab's side rather
than a per-company data issue, but two symbols is not enough evidence to be
certain — it's possible Schwab's calc methodology differs from the
conventional EBIT/interest-expense definition, or that it's genuinely
unpopulated only for this subset of equities. This provider does NOT
silently drop or reinterpret a raw `0.0` — it still populates
`interest_coverage=0.0` in the snapshot (so the value is available
downstream if a caller wants it) but logs a structured warning
(`interest_coverage_suspicious_zero`) whenever the raw value is exactly
`0.0`, so the anomaly is visible/flaggable rather than silently trusted.
Still genuinely UNTESTED: behavior across other equity subtypes/assetTypes
(e.g. non-EQUITY `assetType`, ADRs, etc.) — only plain-equity NASDAQ large
caps were exercised live this session.

The remaining 3 metrics — `fcf_5y_cumulative`, `ocf_ni_ratio`,
`share_dilution_5y` — remain structurally impossible for Schwab to supply:
the live `FundamentalInst` payload above (see the full field list in this
module's git history / the live capture used to write this) is still a
single current/TTM snapshot, with no multi-year income-statement / cash-flow
/ balance-sheet time series anywhere in it. These 3 stay `None` here,
always, regardless of what Schwab returns.

`years_available` stays `0` for THIS PROVIDER'S OWN snapshot, always — even
though 3 real fields are now populated from a live instruments/fundamental
lookup, this provider still has zero years of *historical* data of its own;
`years_available` is meant as a proxy for "how much usable multi-year
history backs this snapshot," and claiming anything above `0` here would
overstate that. The merge layer (`screener.data.factory`'s
`_MergedFundamentalsProvider`) is responsible for using yfinance's
`years_available` in the final merged snapshot handed to the quality gate.

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


class SchwabFundamentalsProvider:
    def __init__(self, client: SchwabClient, cache: FundamentalsCache) -> None:
        self._client = client
        self._cache = cache

    async def get_fundamentals(self, symbol: str) -> FundamentalsSnapshot | None:
        """Return a FundamentalsSnapshot from cache (cache hit -> no network
        call at all), else fetch a live Schwab instruments/fundamental
        lookup and build a snapshot with `interest_coverage`/`gross_margin`/
        `net_margin` populated from the response's `fundamental` section
        (each defensively coerced -- see `_safe_float`; margins additionally
        divided by 100, see module docstring), the other 3 metrics always
        `None`, and `years_available=0` (see module docstring for why).
        Caches and returns it. Always returns a real snapshot — never
        actually returns bare `None`."""
        cached = self._cache.get(symbol)
        if cached is not None:
            return cached

        fundamental = await self._fetch_fundamental(symbol)

        interest_coverage: float | None = None
        gross_margin: float | None = None
        net_margin: float | None = None

        if fundamental is not None:
            interest_coverage = self._safe_float(
                symbol, "interest_coverage", fundamental.get("interestCoverage")
            )
            if interest_coverage == 0.0:
                # See module docstring's CAVEAT: live-observed on both AAPL
                # and MSFT this session, which looks like a systematic
                # Schwab data artifact rather than a genuine 0.0x coverage
                # ratio — flagged, not dropped/converted.
                logger.warning("interest_coverage_suspicious_zero", symbol=symbol)

            gross_margin_pct = self._safe_float(
                symbol, "gross_margin", fundamental.get("grossMarginTTM")
            )
            gross_margin = gross_margin_pct / 100 if gross_margin_pct is not None else None

            net_margin_pct = self._safe_float(
                symbol, "net_margin", fundamental.get("netProfitMarginTTM")
            )
            net_margin = net_margin_pct / 100 if net_margin_pct is not None else None

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

    async def _fetch_fundamental(self, symbol: str) -> dict | None:
        """Fetch a single symbol's Schwab `fundamental` dict via the
        instruments/fundamental-projection endpoint, defensively. Wraps the
        HTTP call + payload parsing in a try/except so a malformed or
        missing Schwab response never crashes the caller — logs a warning
        and returns `None` instead.

        CONFIRMED VIA LIVE API CALL this session: goes through
        `SchwabClient.call()` to schwab-py's typed
        `client.raw.get_instruments(symbols, projection=Instrument.Projection.FUNDAMENTAL)`,
        hitting `/marketdata/v1/instruments?projection=fundamental`. The
        real response envelope is `{"instruments": [{"symbol": ...,
        "fundamental": {...}, ...}, ...]}` — a top-level list, not a dict
        keyed by symbol (unlike `get_quotes`). Since only one symbol is
        requested at a time here, the entry whose own `"symbol"` matches is
        selected (falling back to the first list entry if that lookup
        somehow fails, since a single-symbol request should only ever
        return zero or one entries). See the module docstring for the full
        live-verification writeup, including the confirmed field names
        (`interestCoverage`, `grossMarginTTM`, `netProfitMarginTTM`) and the
        confirmed percentage (not ratio) convention for the two margin
        fields.
        """
        try:
            payload = await self._client.call(
                lambda: self._client.raw.get_instruments(
                    [symbol],
                    projection=self._client.raw.Instrument.Projection.FUNDAMENTAL,
                ),
                label="get_instruments",
            )
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

        instruments = payload.get("instruments")
        if not isinstance(instruments, list) or not instruments:
            logger.warning(
                "fundamentals_metric_unavailable", symbol=symbol, metric="schwab_fundamentals"
            )
            return None

        entry = next(
            (i for i in instruments if isinstance(i, dict) and i.get("symbol") == symbol),
            instruments[0],
        )
        if not isinstance(entry, dict):
            logger.warning(
                "fundamentals_metric_unavailable", symbol=symbol, metric="schwab_fundamentals"
            )
            return None

        fundamental = entry.get("fundamental")
        if not isinstance(fundamental, dict):
            logger.warning(
                "fundamentals_metric_unavailable", symbol=symbol, metric="schwab_fundamentals"
            )
            return None

        return fundamental
