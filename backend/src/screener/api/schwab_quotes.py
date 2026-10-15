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
