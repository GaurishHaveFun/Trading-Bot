"""Stock screener entry point — orchestration and CLI only."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from screener.config import get_settings, load_rules_config
from screener.data import BarCache, YFinanceProvider
from screener.models import Bar, RuleResult, ScreenerRun, Signal
from screener.output import write_run
from screener.rules import RuleEngine
from screener.universe import StaticUniverse
from screener.utils.logging import configure_logging, get_logger

logger = get_logger(__name__)

_RULES_PATH = Path("config/rules.yaml")
_UNIVERSE_PATH = Path("config/universe.yaml")
_CACHE_PATH = Path(".cache/bars.db")
_MIN_BARS = 200
_LOOKBACK_DAYS = 375  # ~250 trading days
_CONCURRENCY = 10


async def run_screener() -> ScreenerRun:
    """Full pipeline: universe → fetch → evaluate → output."""
    settings = get_settings()
    configure_logging(settings.log_level)

    rules_config = load_rules_config(_RULES_PATH)
    universe = StaticUniverse(_UNIVERSE_PATH)
    symbols = universe.get_symbols()

    cache = BarCache(_CACHE_PATH)
    provider = YFinanceProvider(cache)
    engine = RuleEngine(rules_config.rules)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=_LOOKBACK_DAYS)

    logger.info("screener_start", symbols=len(symbols), start=str(start.date()), end=str(end.date()))

    sem = asyncio.Semaphore(_CONCURRENCY)

    async def fetch_one(symbol: str) -> tuple[str, list[Bar]]:
        async with sem:
            bars = await provider.get_bars(symbol, start, end)
            return symbol, bars

    results = await asyncio.gather(*[fetch_one(s) for s in symbols], return_exceptions=True)

    signals: list[Signal] = []
    run_ts = datetime.now(timezone.utc)

    for item in results:
        if isinstance(item, Exception):
            logger.warning("fetch_error", error=str(item))
            continue
        symbol, bars = item
        if len(bars) < _MIN_BARS:
            logger.warning("insufficient_bars", symbol=symbol, count=len(bars))
            continue

        rule_results = engine.evaluate(symbol, bars)
        score = engine.score(rule_results)
        rules_passed = sum(1 for r in rule_results if r.passed)

        snapshot = _build_snapshot(bars, rule_results)

        signal = Signal(
            ticker=symbol,
            timestamp=bars[-1].timestamp,
            score=score,
            rules_passed=rules_passed,
            rules_total=len(rule_results),
            rule_results=rule_results,
            snapshot=snapshot,
        )
        signals.append(signal)

    signals.sort(key=lambda s: s.score, reverse=True)

    run = ScreenerRun(
        run_timestamp=run_ts,
        universe="static",
        alert_threshold=settings.alert_threshold,
        signals=signals,
    )

    path = write_run(run)

    above = [s for s in signals if s.score >= settings.alert_threshold]
    top5 = signals[:5]

    logger.info(
        "screener_complete",
        total_symbols=len(symbols),
        signals_evaluated=len(signals),
        above_threshold=len(above),
        top5=[f"{s.ticker}={s.score:.2f}" for s in top5],
        output=str(path),
    )

    cache.close()
    return run


def _build_snapshot(bars: list[Bar], rule_results: list[RuleResult]) -> dict:
    """Build the snapshot dict from the last bar and rule detail values."""
    from screener.indicators.library import latest_close, latest_volume, sma, rsi, atr
    import pandas as pd

    df = pd.DataFrame([{
        "timestamp": b.timestamp, "open": b.open, "high": b.high,
        "low": b.low, "close": b.close, "volume": b.volume,
    } for b in bars]).sort_values("timestamp").reset_index(drop=True)

    snapshot: dict = {
        "close": round(latest_close(df), 4),
        "volume": latest_volume(df),
    }
    try:
        snapshot["rsi_14"] = round(rsi(df, 14), 4)
    except Exception:
        pass
    try:
        snapshot["sma_50"] = round(sma(df, 50), 4)
    except Exception:
        pass
    try:
        snapshot["sma_200"] = round(sma(df, 200), 4)
    except Exception:
        pass
    try:
        snapshot["atr_14"] = round(atr(df, 14), 4)
    except Exception:
        pass
    return snapshot


def _print_ticker_breakdown(symbol: str, signal: Signal) -> None:
    """Print a readable per-rule breakdown for --ticker mode."""
    print(f"\n{'='*60}")
    print(f"  {symbol}  |  score: {signal.score:.2%}  |  {signal.rules_passed}/{signal.rules_total} rules passed")
    print(f"{'='*60}")
    print(f"  Snapshot: {signal.snapshot}")
    print(f"  {'Rule':<25} {'Pass':>6} {'Weight':>8}  Detail")
    print(f"  {'-'*55}")
    for r in signal.rule_results:
        status = "✓" if r.passed else "✗"
        print(f"  {r.rule_name:<25} {status:>6} {r.weight:>8.1f}  {r.detail}")
    print()


async def run_ticker_debug(symbol: str) -> None:
    """Debug mode: fetch + evaluate a single ticker and print a verbose breakdown."""
    settings = get_settings()
    configure_logging(settings.log_level)

    rules_config = load_rules_config(_RULES_PATH)
    cache = BarCache(_CACHE_PATH)
    provider = YFinanceProvider(cache)
    engine = RuleEngine(rules_config.rules)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=_LOOKBACK_DAYS)

    bars = await provider.get_bars(symbol, start, end)
    if len(bars) < _MIN_BARS:
        print(f"[WARN] Only {len(bars)} bars for {symbol} — need at least {_MIN_BARS}")
        cache.close()
        return

    rule_results = engine.evaluate(symbol, bars)
    score = engine.score(rule_results)
    rules_passed = sum(1 for r in rule_results if r.passed)
    snapshot = _build_snapshot(bars, rule_results)

    signal = Signal(
        ticker=symbol,
        timestamp=bars[-1].timestamp,
        score=score,
        rules_passed=rules_passed,
        rules_total=len(rule_results),
        rule_results=rule_results,
        snapshot=snapshot,
    )

    _print_ticker_breakdown(symbol, signal)
    cache.close()


def _start_scheduler() -> None:
    """Start the APScheduler cron loop (imported lazily to keep main.py clean)."""
    from screener.scheduler import start  # implemented in Step 9
    start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Stock screener")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true", help="Run once and exit")
    group.add_argument("--ticker", metavar="SYMBOL", help="Debug a single ticker")
    args = parser.parse_args()

    if args.once:
        asyncio.run(run_screener())
    elif args.ticker:
        asyncio.run(run_ticker_debug(args.ticker.upper()))
    else:
        _start_scheduler()


if __name__ == "__main__":
    main()
