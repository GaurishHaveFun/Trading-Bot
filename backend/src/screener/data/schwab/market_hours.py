"""Pre-run market-hours gate (Schwab-backed) — used ONLY to skip SCHEDULED
(cron) screener runs when the equity market is closed today (weekend/
holiday), so a scheduled run never scores stale/pre-market data. Manual
invocations (`--once`, `--ticker`) never call this module at all — that
wiring lives in `screener.main`/`screener.scheduler`, not here.

CONFIRMED (verified by reading the installed schwab-py source directly,
`.venv/lib/python3.12/site-packages/schwab/client/base.py` lines ~1042-1065):
  - `BaseClient.MarketHours.Market` is a nested `Enum` with `EQUITY = 'equity'`
    (plus OPTION/BOND/FUTURE/FOREX) — reachable off an `AsyncClient` instance
    as `client.raw.MarketHours.Market.EQUITY` since `AsyncClient` subclasses
    `BaseClient` and does not shadow this nested class.
  - `AsyncClient.get_market_hours(markets, *, date=None)` is a pure
    passthrough: it converts `markets` via `convert_enum_iterable`, builds
    `params={'markets': ','.join(markets)}` (plus `date` if given), and calls
    `self._get_request('/marketdata/v1/markets', params)`. No response
    parsing happens on schwab-py's side — whatever Schwab returns is handed
    back as-is (after `SchwabClient.call()`'s own `.raise_for_status()` +
    `.json()`).

UNVERIFIED (no live Schwab credentials this session — there is no sample
response anywhere in the installed package or its tests): the exact response
SHAPE/field names. This module codes defensively against Schwab's documented
convention (the same convention inherited from the old TDAmeritrade API this
endpoint descends from): a dict keyed by market category (e.g. `"equity"`),
whose value is a dict keyed by product code (e.g. `"EQ"`), each an object
carrying a boolean `isOpen`, e.g.:

    {"equity": {"EQ": {"isOpen": true, ...}}}

Any deviation from that shape (missing keys, wrong types, empty dicts) is
treated as "unable to confirm the market is open" and, per this module's
fail-open policy below, resolves to `True` (allow the run) rather than
`False` — a missed scheduled run is worse than one unnecessary run.
"""
from __future__ import annotations

from screener.config import Settings
from screener.data.factory import _build_schwab_auth
from screener.data.schwab.client import SchwabClient
from screener.utils.logging import get_logger

logger = get_logger(__name__)


async def is_equity_market_open(settings: Settings) -> bool:
    """Best-effort check of whether the equity market is open today, via
    Schwab's `get_market_hours` endpoint. Fails OPEN (returns `True`) on
    every error path — see the module docstring for the rationale — so this
    function should never be the reason a scheduled run is skipped due to a
    transient Schwab problem; it can only skip a run when Schwab positively
    reports the market as closed.
    """
    if settings.data_provider != "schwab":
        # No Schwab client available to ask (e.g. yfinance-only config) —
        # fail open rather than block scheduled runs on a check we have no
        # way to perform.
        logger.info(
            "market_hours_check_skipped",
            reason="data_provider is not schwab",
            data_provider=settings.data_provider,
        )
        return True

    client: SchwabClient | None = None
    try:
        auth = _build_schwab_auth(settings)
        client = SchwabClient(auth)
        response = await client.call(
            lambda: client.raw.get_market_hours([client.raw.MarketHours.Market.EQUITY]),
            label="get_market_hours",
        )

        equity = response["equity"]
        # Walk to the first/any nested product dict (e.g. "EQ") rather than
        # hardcoding the product code, since that key is UNVERIFIED — see
        # module docstring.
        product = next(iter(equity.values()))
        is_open = product.get("isOpen")
        if not isinstance(is_open, bool):
            raise ValueError(f"isOpen field missing or non-bool: {is_open!r}")

        logger.info("market_hours_check", is_open=is_open)
        return is_open
    except Exception as exc:
        logger.warning("market_hours_check_failed", error=str(exc))
        return True
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception as exc:
                logger.warning("market_hours_client_close_failed", error=str(exc))
