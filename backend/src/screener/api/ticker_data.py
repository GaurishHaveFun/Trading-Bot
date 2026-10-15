"""Shared yfinance `info`-dict parsing layer used by both the
`/tickers/{symbol}` route in `screener.api.app` and the (upcoming) AI
"why is this stock falling?" analysis module. New callers must reuse
`fetch_quote_info` rather than introducing a second single-symbol lookup."""
from __future__ import annotations

import math

import yfinance as yf
from pydantic import BaseModel


class TickerProfileOut(BaseModel):
    long_name: str | None
    short_name: str | None
    business_summary: str | None
    sector: str | None
    industry: str | None
    employees: int | None
    website: str | None
    country: str | None
    exchange: str | None
    currency: str | None


class TickerStatsOut(BaseModel):
    price: float | None
    change_pct: float | None
    previous_close: float | None
    open: float | None
    day_high: float | None
    day_low: float | None
    market_cap: float | None
    trailing_pe: float | None
    forward_pe: float | None
    price_to_book: float | None
    eps_trailing: float | None
    eps_forward: float | None
    dividend_yield: float | None
    beta: float | None
    fifty_two_week_high: float | None
    fifty_two_week_low: float | None
    volume: float | None
    avg_volume: float | None
    avg_volume_10d: float | None


class TickerTargetsOut(BaseModel):
    mean: float | None
    high: float | None
    low: float | None
    median: float | None
    recommendation_key: str | None
    analyst_count: int | None


def pick(info: dict, *keys: str):
    """First key present in `info` with a non-`None` value. Deliberately not
    `info.get(a) or info.get(b)` — that idiom (used by `/quotes` and
    `/losers` above) silently discards a legitimate `0.0`/`""`/`False`."""
    for key in keys:
        value = info.get(key)
        if value is not None:
            return value
    return None


def num(value) -> float | None:
    """Coerce `value` to `float`, returning `None` for anything non-numeric,
    `NaN`, or `±inf`. yfinance routinely puts `float('nan')` into `info`
    (e.g. `trailingPE` for a loss-making company); Pydantic happily accepts
    NaN into a `float` field and FastAPI then emits a bare `NaN` token, which
    is invalid JSON. Every numeric field on the `/tickers/{symbol}` response
    goes through this helper."""
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def looks_like_real_symbol(info: dict) -> bool:
    """True if `info` carries any of the fields yfinance only populates for a
    real, tradeable symbol. yfinance returns a small non-empty dict even for
    garbage symbols, so truthiness of `info` alone is not a valid check --
    this is what turns `/tickers/ZZZZZZ` into a clean 404."""
    return any(
        info.get(key) is not None
        for key in ("regularMarketPrice", "currentPrice", "longName", "shortName")
    )


def to_profile(info: dict) -> TickerProfileOut:
    employees = pick(info, "fullTimeEmployees")
    return TickerProfileOut(
        long_name=pick(info, "longName"),
        short_name=pick(info, "shortName"),
        business_summary=pick(info, "longBusinessSummary"),
        sector=pick(info, "sector", "sectorDisp"),
        industry=pick(info, "industry", "industryKey"),
        employees=int(employees) if employees is not None else None,
        website=pick(info, "website"),
        country=pick(info, "country"),
        exchange=pick(info, "fullExchangeName", "exchange"),
        currency=pick(info, "currency"),
    )


def to_stats(info: dict) -> TickerStatsOut:
    return TickerStatsOut(
        price=num(pick(info, "regularMarketPrice", "currentPrice")),
        change_pct=num(pick(info, "regularMarketChangePercent")),
        previous_close=num(pick(info, "regularMarketPreviousClose", "previousClose")),
        open=num(pick(info, "regularMarketOpen", "open")),
        day_high=num(pick(info, "regularMarketDayHigh", "dayHigh")),
        day_low=num(pick(info, "regularMarketDayLow", "dayLow")),
        market_cap=num(pick(info, "marketCap")),
        trailing_pe=num(pick(info, "trailingPE")),
        forward_pe=num(pick(info, "forwardPE")),
        price_to_book=num(pick(info, "priceToBook")),
        eps_trailing=num(pick(info, "trailingEps")),
        eps_forward=num(pick(info, "forwardEps")),
        dividend_yield=num(pick(info, "dividendYield", "trailingAnnualDividendYield")),
        beta=num(pick(info, "beta")),
        fifty_two_week_high=num(pick(info, "fiftyTwoWeekHigh")),
        fifty_two_week_low=num(pick(info, "fiftyTwoWeekLow")),
        volume=num(pick(info, "regularMarketVolume", "volume")),
        avg_volume=num(pick(info, "averageVolume")),
        avg_volume_10d=num(pick(info, "averageVolume10days", "averageDailyVolume10Day")),
    )


def to_targets(info: dict) -> TickerTargetsOut:
    analyst_count = pick(info, "numberOfAnalystOpinions")
    return TickerTargetsOut(
        mean=num(pick(info, "targetMeanPrice")),
        high=num(pick(info, "targetHighPrice")),
        low=num(pick(info, "targetLowPrice")),
        median=num(pick(info, "targetMedianPrice")),
        recommendation_key=pick(info, "recommendationKey"),
        analyst_count=int(analyst_count) if analyst_count is not None else None,
    )


def fetch_quote_info(symbol: str) -> dict | None:
    """Blocking single-symbol yfinance lookup. Mirrors
    `LosersUniverse._fetch_quote`/`factory._enrich_industry_sector`'s
    identical `yf.Ticker(symbol).info` pattern — call via `asyncio.to_thread`
    only. Returns `None` on any fetch error (logged by the caller)."""
    return yf.Ticker(symbol).info
