"""Recent-news fetch for a single ticker, used to feed the AI "why is this
stock falling?" analysis engine. Reuses yfinance's `.news` property directly
(there is no shared multi-symbol news layer to reuse) -- always call
`fetch_news` via its own internal `asyncio.to_thread` wrapping, never call
`yf.Ticker(...).news` directly from other modules."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import yfinance as yf

from screener.analysis.models import NewsItem
from screener.utils.logging import get_logger

logger = get_logger(__name__)


def _parse_timestamp(epoch: object, iso: object) -> datetime | None:
    """Coerce either an epoch-seconds value or an ISO 8601 string into a UTC
    datetime. Returns None if neither is usable."""
    if epoch is not None:
        try:
            return datetime.fromtimestamp(float(epoch), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            pass
    if isinstance(iso, str) and iso:
        try:
            parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _normalize_item(raw: dict) -> NewsItem | None:
    """Normalize one raw yfinance `.news` entry, handling both the flat and
    nested ("content") payload shapes. Returns None if there's no usable
    title -- title is mandatory."""
    if not isinstance(raw, dict):
        return None

    content = raw.get("content")
    nested = content if isinstance(content, dict) else {}

    title = raw.get("title") or nested.get("title")
    if not title or not isinstance(title, str):
        return None

    publisher = raw.get("publisher")
    if publisher is None:
        provider = nested.get("provider")
        if isinstance(provider, dict):
            publisher = provider.get("displayName")

    url = raw.get("link")
    if url is None:
        canonical = nested.get("canonicalUrl")
        if isinstance(canonical, dict):
            url = canonical.get("url")

    summary = raw.get("summary")
    if summary is None:
        summary = nested.get("summary")

    published_at = _parse_timestamp(
        raw.get("providerPublishTime"), nested.get("pubDate")
    )

    return NewsItem(
        title=title,
        publisher=publisher if isinstance(publisher, str) else None,
        url=url if isinstance(url, str) else None,
        published_at=published_at,
        summary=summary if isinstance(summary, str) else None,
    )


async def fetch_news(symbol: str, days: int = 7, limit: int = 10) -> list[NewsItem]:
    """Fetch and normalize up to `limit` recent news items for `symbol`,
    dropping anything older than `days` days. Never raises -- any failure
    (network error, garbage payload, missing `.news`) is logged and results
    in an empty list.

    Items with no parseable timestamp are dropped along with items strictly
    older than the window, since we can't prove an unparseable-timestamp
    item is actually within the requested window (documented, consistent
    behavior rather than silently keeping possibly-stale items)."""
    try:
        raw_items = await asyncio.to_thread(lambda: yf.Ticker(symbol).news)
    except Exception as exc:
        logger.warning("analysis_news_error", ticker=symbol, error=str(exc))
        return []

    if not raw_items:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    normalized: list[NewsItem] = []
    try:
        for raw in raw_items:
            try:
                item = _normalize_item(raw)
            except Exception as exc:
                logger.warning(
                    "analysis_news_item_error", ticker=symbol, error=str(exc)
                )
                continue
            if item is None:
                continue
            if item.published_at is None or item.published_at < cutoff:
                continue
            normalized.append(item)
    except Exception as exc:
        logger.warning("analysis_news_error", ticker=symbol, error=str(exc))
        return []

    normalized.sort(key=lambda i: i.published_at, reverse=True)
    return normalized[:limit]
