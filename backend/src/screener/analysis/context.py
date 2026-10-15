"""Evidence assembly for the AI "why is this stock falling?" analysis engine.
Gathers everything the LLM needs to explain a price move -- quote/profile
info, technicals derived from cached bars, sector peers, and recent news --
into one `AnalysisEvidence` object, then flattens it into a deterministic
prompt payload.

Deliberately does NOT import `screener.api.app` (that's a separate module
owned by a later build step; importing it here would create a fragile
coupling and a possible circular-import risk). The peers lookup below
duplicates `app.py`'s watchlist-load + `LosersUniverse` construction pattern
directly instead."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from screener.analysis.models import AnalysisEvidence, NewsItem, PeerMove
from screener.analysis.news import fetch_news
from screener.api.ticker_data import (
    fetch_quote_info,
    looks_like_real_symbol,
    num,
    to_profile,
    to_stats,
    to_targets,
)
from screener.config import get_settings, load_watchlist
from screener.data.cache import BarCache
from screener.data.factory import build_bar_provider
from screener.indicators.library import high_52w, low_52w, rsi, sma_volume
from screener.universe.losers import LosersUniverse
from screener.utils.logging import get_logger

logger = get_logger(__name__)

_BAR_CACHE_PATH = Path(".cache/bars.db")
_TECHNICALS_LOOKBACK_DAYS = 400
_MAX_PEERS = 5

# Lazily-built module-level singletons, mirroring `screener.api.app`'s
# `_get_bar_provider()` pattern -- built on first use (not at import time)
# so `get_settings()` stays monkeypatchable, and reused across calls so
# repeated `build_evidence` calls don't reopen the SQLite cache file.
_bar_cache: BarCache | None = None
_bar_provider = None


def _get_bar_provider():
    global _bar_cache, _bar_provider
    if _bar_provider is None:
        if _bar_cache is None:
            _bar_cache = BarCache(_BAR_CACHE_PATH)
        _bar_provider = build_bar_provider(get_settings(), _bar_cache)
    return _bar_provider


def _bars_to_frame(bars: list) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
        }
    )


async def _compute_technicals(ticker: str) -> dict[str, Any]:
    """Fetch ~400 days of bars and derive a technicals dict. Every value is
    passed through `num()` before inclusion so a NaN/None result from a
    too-short history is dropped (never included as NaN), per CLAUDE.md's
    non-negotiable #5. Wrapped end-to-end so a total failure (e.g.
    `get_bars` raising) degrades to `{}` rather than propagating."""
    technicals: dict[str, Any] = {}
    try:
        provider = _get_bar_provider()
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=_TECHNICALS_LOOKBACK_DAYS)
        bars = await provider.get_bars(ticker, start, end)
        if len(bars) < 2:
            return {}

        df = _bars_to_frame(bars)
        latest_close = float(df["close"].iloc[-1])

        for n, key in ((1, "change_pct_1d"), (5, "change_pct_5d"), (21, "change_pct_21d")):
            if len(df) >= n + 1:
                try:
                    prior = float(df["close"].iloc[-1 - n])
                    if prior:
                        value = num((latest_close - prior) / prior * 100)
                        if value is not None:
                            technicals[key] = value
                except Exception as exc:
                    logger.warning(
                        "analysis_technicals_error", ticker=ticker, field=key, error=str(exc)
                    )

        if len(df) >= 15:
            try:
                value = num(rsi(df, 14))
                if value is not None:
                    technicals["rsi_14"] = value
            except Exception as exc:
                logger.warning(
                    "analysis_technicals_error", ticker=ticker, field="rsi_14", error=str(exc)
                )

        try:
            low_val = low_52w(df, 252)
            if low_val:
                value = num((latest_close - low_val) / low_val * 100)
                if value is not None:
                    technicals["pct_above_52w_low"] = value
        except Exception as exc:
            logger.warning(
                "analysis_technicals_error", ticker=ticker, field="pct_above_52w_low", error=str(exc)
            )

        try:
            high_val = high_52w(df, 252)
            if high_val:
                value = num((high_val - latest_close) / high_val * 100)
                if value is not None:
                    technicals["pct_below_52w_high"] = value
        except Exception as exc:
            logger.warning(
                "analysis_technicals_error", ticker=ticker, field="pct_below_52w_high", error=str(exc)
            )

        latest_volume = int(df["volume"].iloc[-1])
        technicals["latest_volume"] = latest_volume
        if len(df) >= 10:
            try:
                avg_volume = sma_volume(df, 10)
                if avg_volume:
                    value = num((latest_volume - avg_volume) / avg_volume * 100)
                    if value is not None:
                        technicals["volume_vs_avg_10d_pct"] = value
            except Exception as exc:
                logger.warning(
                    "analysis_technicals_error",
                    ticker=ticker,
                    field="volume_vs_avg_10d_pct",
                    error=str(exc),
                )
    except Exception as exc:
        logger.warning("analysis_technicals_error", ticker=ticker, error=str(exc))
        return {}

    return technicals


async def _compute_peers(ticker: str, own_sector: str | None) -> list[PeerMove]:
    """Sector peers pulled from the same-day losers universe. Returns []
    when the ticker has no known sector (nothing to compare against)."""
    if not own_sector:
        return []
    try:
        settings = get_settings()
        try:
            watchlist = load_watchlist(settings.watchlist_path)
        except Exception as exc:
            logger.warning("analysis_watchlist_load_error", ticker=ticker, error=str(exc))
            watchlist = set()

        universe = LosersUniverse(watchlist=watchlist)
        await universe.get_symbols()
        quotes = await universe.get_quotes()

        candidates = [
            (t, meta)
            for t, meta in quotes.items()
            if t != ticker and meta.get("sector") == own_sector
        ]
        candidates.sort(key=lambda kv: kv[1].get("change_pct") or 0.0)
        top = candidates[:_MAX_PEERS]
        return [
            PeerMove(ticker=t, change_pct=meta.get("change_pct"), sector=meta.get("sector"))
            for t, meta in top
        ]
    except Exception as exc:
        logger.warning("analysis_evidence_partial", ticker=ticker, source="peers", error=str(exc))
        return []


async def _fetch_news_safe(ticker: str) -> list[NewsItem]:
    try:
        return await fetch_news(ticker)
    except Exception as exc:
        logger.warning("analysis_evidence_partial", ticker=ticker, source="news", error=str(exc))
        return []


async def _compute_technicals_safe(ticker: str) -> dict[str, Any]:
    try:
        return await _compute_technicals(ticker)
    except Exception as exc:
        logger.warning(
            "analysis_evidence_partial", ticker=ticker, source="technicals", error=str(exc)
        )
        return {}


async def _compute_peers_safe(ticker: str, own_sector: str | None) -> list[PeerMove]:
    try:
        return await _compute_peers(ticker, own_sector)
    except Exception as exc:
        logger.warning("analysis_evidence_partial", ticker=ticker, source="peers", error=str(exc))
        return []


async def build_evidence(ticker: str) -> AnalysisEvidence | None:
    """Assemble the full evidence bundle for `ticker`. Returns None only when
    the symbol itself is unknown/unreal; any individual source (technicals,
    peers, news) degrading on its own failure does not fail the whole
    function -- it just contributes an empty/default value."""
    ticker = ticker.strip().upper()

    info = await asyncio.to_thread(fetch_quote_info, ticker)
    if info is None or not looks_like_real_symbol(info):
        logger.warning("analysis_unknown_symbol", ticker=ticker)
        return None

    profile = to_profile(info)
    stats = to_stats(info)
    targets = to_targets(info)

    technicals_result, peers_result, news_result = await asyncio.gather(
        _compute_technicals_safe(ticker),
        _compute_peers_safe(ticker, profile.sector),
        _fetch_news_safe(ticker),
        return_exceptions=True,
    )

    technicals: dict[str, Any] = technicals_result if isinstance(technicals_result, dict) else {}
    if isinstance(technicals_result, Exception):
        logger.warning(
            "analysis_evidence_partial", ticker=ticker, source="technicals", error=str(technicals_result)
        )
        technicals = {}

    peers: list[PeerMove] = peers_result if isinstance(peers_result, list) else []
    if isinstance(peers_result, Exception):
        logger.warning("analysis_evidence_partial", ticker=ticker, source="peers", error=str(peers_result))
        peers = []

    news: list[NewsItem] = news_result if isinstance(news_result, list) else []
    if isinstance(news_result, Exception):
        logger.warning("analysis_evidence_partial", ticker=ticker, source="news", error=str(news_result))
        news = []

    return AnalysisEvidence(
        ticker=ticker,
        as_of=datetime.now(timezone.utc),
        profile=profile.model_dump(),
        stats=stats.model_dump(),
        targets=targets.model_dump(),
        technicals=technicals,
        peers=peers,
        news=news,
    )


def evidence_to_prompt_payload(evidence: AnalysisEvidence) -> dict:
    """Flatten an `AnalysisEvidence` into a plain, deterministically-ordered
    dict suitable for JSON-serializing into the analysis prompt. Deterministic
    key order matters for prompt caching -- identical evidence must always
    serialize to identical bytes so repeated requests hit the cache. Pure
    function, no I/O."""
    return {
        "ticker": evidence.ticker,
        "as_of": evidence.as_of.isoformat(),
        "profile": dict(sorted(evidence.profile.items())),
        "stats": dict(sorted(evidence.stats.items())),
        "targets": dict(sorted(evidence.targets.items())),
        "technicals": dict(sorted(evidence.technicals.items())),
        "peers": [dict(sorted(p.model_dump().items())) for p in evidence.peers],
        "news": [dict(sorted(n.model_dump(mode="json").items())) for n in evidence.news],
    }
