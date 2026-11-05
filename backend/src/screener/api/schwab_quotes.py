"""Schwab-backed quote + static sector lookups for the read-only API.

Yahoo rejects datacenter IPs (Render) for `yf.Ticker(...).info`, so
`/quotes` and `/losers` source price / previous close / change% from
Schwab's quotes endpoint instead, and only fall back to yfinance when
Schwab fails. Schwab has no sector field (see
`screener.data.schwab.universe`), so sector comes from a small static map
covering the watchlist names; other symbols fall back to yfinance."""
from __future__ import annotations

from screener.config import get_settings
from screener.data.schwab.auth import SchwabAuth
from screener.data.schwab.client import SchwabClient
from screener.utils.logging import get_logger

logger = get_logger(__name__)

_BATCH_SIZE = 50

# Sector names follow Yahoo's naming so they match the yfinance fallback.
WATCHLIST_SECTORS: dict[str, str] = {
    "AAPL": "Technology",
    "MSFT": "Technology",
    "GOOGL": "Communication Services",
    "AMZN": "Consumer Cyclical",
    "META": "Communication Services",
    "NVDA": "Technology",
    "TSLA": "Consumer Cyclical",
    "AMD": "Technology",
    "AVGO": "Technology",
    "TSM": "Technology",
    "INTC": "Technology",
    "QCOM": "Technology",
    "MU": "Technology",
    "ASML": "Technology",
    "ARM": "Technology",
    "SMCI": "Technology",
}


def static_sector(symbol: str) -> str | None:
    return WATCHLIST_SECTORS.get(symbol.upper())


def shape_quote_entry(entry: dict) -> dict | None:
    """Convert one Schwab quotes entry into the yfinance-info-style dict the
    API handlers already read. Fields (QuoteEquity): `lastPrice` (falls back
    to `mark`), `closePrice` (previous close), `netPercentChange`."""
    quote = entry.get("quote") or {}
    price = quote.get("lastPrice")
    if price is None:
        price = quote.get("mark")
    if price is None:
        return None
    return {
        "regularMarketPrice": price,
        "regularMarketPreviousClose": quote.get("closePrice"),
        "regularMarketChangePercent": quote.get("netPercentChange"),
        "currency": "USD",
    }


async def fetch_schwab_quotes(symbols: list[str]) -> dict[str, dict]:
    """Batched Schwab quote lookup, keyed by upper-case symbol. Raises on
    auth/network failure (callers fall back to yfinance); symbols Schwab
    doesn't return are simply absent."""
    if not symbols:
        return {}
    settings = get_settings()
    auth = SchwabAuth(
        app_key=settings.schwab_app_key,
        app_secret=settings.schwab_app_secret,
        callback_url=settings.schwab_callback_url,
        token_path=settings.schwab_token_path,
    )
    client = SchwabClient(auth)
    unique = list(dict.fromkeys(s.upper() for s in symbols))
    result: dict[str, dict] = {}
    try:
        fields = client.raw.Quote.Fields
        for i in range(0, len(unique), _BATCH_SIZE):
            batch = unique[i : i + _BATCH_SIZE]
            payload = await client.call(
                lambda b=batch: client.raw.get_quotes(b, fields=[fields.QUOTE]),
                label="get_quotes",
            )
            if not isinstance(payload, dict):
                continue
            for symbol, entry in payload.items():
                if isinstance(entry, dict):
                    shaped = shape_quote_entry(entry)
                    if shaped:
                        result[symbol.upper()] = shaped
    finally:
        try:
            await client.aclose()
        except Exception as exc:
            logger.debug("schwab_client_close_error", error=str(exc))
    return result


def shape_instrument_entry(entry: dict) -> dict | None:
    """Convert one Schwab instruments (FUNDAMENTAL projection) entry into the
    yfinance-info-style keys `ticker_data.to_stats`/`to_profile` read. Only
    fields Schwab actually supplies are emitted; zeros/None are skipped for
    fields where 0 means "unpopulated" (PE, EPS aside). Returns None if
    nothing useful is present."""
    fundamental = entry.get("fundamental")
    if not isinstance(fundamental, dict):
        fundamental = {}
    mapping = {
        "marketCap": "marketCap",
        "trailingPE": "peRatio",
        "trailingEps": "eps",
        "priceToBook": "pbRatio",
        "fiftyTwoWeekHigh": "high52",
        "fiftyTwoWeekLow": "low52",
        "dividendYield": "dividendYield",
        "beta": "beta",
        "averageVolume": "vol3MonthAvg",
        "averageVolume10days": "vol10DayAvg",
    }
    shaped: dict = {}
    for out_key, src_key in mapping.items():
        value = fundamental.get(src_key)
        if value is None:
            continue
        if out_key in ("marketCap", "averageVolume", "averageVolume10days", "fiftyTwoWeekHigh",
                       "fiftyTwoWeekLow", "priceToBook", "trailingPE") and not value:
            continue
        shaped[out_key] = value
    description = entry.get("description")
    if isinstance(description, str) and description.strip():
        shaped["shortName"] = description.strip()
        shaped["longName"] = description.strip()
    return shaped or None


async def fetch_schwab_fundamentals(symbol: str) -> dict | None:
    """Single-symbol Schwab instruments/fundamental lookup, shaped via
    `shape_instrument_entry`. Raises on auth/network failure (callers catch);
    returns None when Schwab has no entry for the symbol."""
    settings = get_settings()
    auth = SchwabAuth(
        app_key=settings.schwab_app_key,
        app_secret=settings.schwab_app_secret,
        callback_url=settings.schwab_callback_url,
        token_path=settings.schwab_token_path,
    )
    client = SchwabClient(auth)
    try:
        payload = await client.call(
            lambda: client.raw.get_instruments(
                [symbol.upper()],
                projection=client.raw.Instrument.Projection.FUNDAMENTAL,
            ),
            label="get_instruments",
        )
    finally:
        try:
            await client.aclose()
        except Exception as exc:
            logger.debug("schwab_client_close_error", error=str(exc))
    instruments = payload.get("instruments") if isinstance(payload, dict) else None
    if not isinstance(instruments, list) or not instruments:
        return None
    entry = next(
        (i for i in instruments if isinstance(i, dict) and str(i.get("symbol", "")).upper() == symbol.upper()),
        instruments[0],
    )
    if not isinstance(entry, dict):
        return None
    return shape_instrument_entry(entry)
