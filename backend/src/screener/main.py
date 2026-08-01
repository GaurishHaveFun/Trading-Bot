"""Stock screener entry point — orchestration and CLI only."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from screener.backtest import run_backtest
from screener.config import get_settings, load_rules_config, load_watchlist
from screener.data import BarCache, FundamentalsCache
from screener.data.factory import (
    build_bar_provider,
    build_fundamentals_provider,
    build_quote_lookup_provider,
    build_universe_provider,
)
from screener.data.schwab import SchwabAuth
from screener.models import Bar, BacktestResult, FundamentalsSnapshot, RuleResult, ScreenerRun, Signal
from screener.output import (
    write_backtest_report,
    write_backtest_to_db,
    write_report,
    write_run,
    write_run_to_db,
    write_ticker_report,
)
from screener.rules import RuleEngine
from screener.utils.logging import configure_logging, get_logger

logger = get_logger(__name__)

_RULES_PATH = Path("config/rules.yaml")
_CACHE_PATH = Path(".cache/bars.db")
_FUNDAMENTALS_CACHE_PATH = Path(".cache/fundamentals.db")
_MIN_BARS = 200
_LOOKBACK_DAYS = 375  # ~250 trading days
_CONCURRENCY = 10


async def run_screener(*, check_market_hours: bool = False) -> ScreenerRun | None:
    """Full pipeline: universe → fetch → evaluate → output.

    `check_market_hours` gates this run on the equity market actually being
    open today (weekend/holiday skip). It is only ever set `True` by the
    cron scheduler (`screener.scheduler`) — manual invocations (`--once`,
    `--ticker`) always leave it at the default `False` so they run
    unconditionally. The actual open/closed determination lives in
    `screener.data.schwab.market_hours.is_equity_market_open`; this is just
    the one-line orchestration call to it.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    if check_market_hours:
        from screener.data.schwab.market_hours import is_equity_market_open

        if not await is_equity_market_open(settings):
            logger.info("market_closed_skip_run")
            return None

    rules_config = load_rules_config(_RULES_PATH)
    watchlist = load_watchlist(settings.watchlist_path)
    universe = build_universe_provider(settings, watchlist)
    symbols = await universe.get_symbols()
    quotes = await universe.get_quotes()

    cache = BarCache(_CACHE_PATH)
    provider = build_bar_provider(settings, cache)
    fundamentals_cache = FundamentalsCache(_FUNDAMENTALS_CACHE_PATH)
    fundamentals_provider = build_fundamentals_provider(settings, fundamentals_cache)
    engine = RuleEngine(rules_config.rules)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=_LOOKBACK_DAYS)

    logger.info("screener_start", symbols=len(symbols), start=str(start.date()), end=str(end.date()))

    sem = asyncio.Semaphore(_CONCURRENCY)

    async def fetch_one(symbol: str) -> tuple[str, list[Bar], FundamentalsSnapshot | None]:
        async with sem:
            bars, funda_snapshot = await asyncio.gather(
                provider.get_bars(symbol, start, end),
                fundamentals_provider.get_fundamentals(symbol),
            )
            return symbol, bars, funda_snapshot

    results = await asyncio.gather(*[fetch_one(s) for s in symbols], return_exceptions=True)

    signals: list[Signal] = []
    signal_bars: dict[str, list[Bar]] = {}
    run_ts = datetime.now(timezone.utc)

    for item in results:
        if isinstance(item, Exception):
            logger.warning("fetch_error", error=str(item))
            continue
        symbol, bars, funda_snapshot = item
        if len(bars) < _MIN_BARS:
            logger.warning("insufficient_bars", symbol=symbol, count=len(bars))
            continue

        meta = quotes.get(symbol)
        rule_results = engine.evaluate(symbol, bars, meta=meta, watchlist=watchlist)
        score = engine.score(rule_results)
        rules_passed = sum(1 for r in rule_results if r.passed)

        snapshot = _build_snapshot(bars, rule_results, meta=meta, symbol=symbol, watchlist=watchlist)

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
        signal_bars[symbol] = bars

    signals.sort(key=lambda s: s.score, reverse=True)

    run = ScreenerRun(
        run_timestamp=run_ts,
        universe=settings.universe,
        alert_threshold=settings.alert_threshold,
        signals=signals,
    )

    path = write_run(run)
    report_path = write_report(
        run, rule_descriptions={r.name: r.description for r in rules_config.rules}
    )

    if settings.database_url:
        try:
            await write_run_to_db(run, bars_by_ticker=signal_bars, database_url=settings.database_url)
        except Exception as exc:
            logger.warning("db_write_failed", error=str(exc))

    above = [s for s in signals if s.score >= settings.alert_threshold]
    top5 = signals[:5]

    logger.info(
        "screener_complete",
        total_symbols=len(symbols),
        signals_evaluated=len(signals),
        above_threshold=len(above),
        top5=[f"{s.ticker}={s.score:.2f}" for s in top5],
        output=str(path),
        report=str(report_path),
    )

    cache.close()
    fundamentals_cache.close()
    return run


def _build_snapshot(
    bars: list[Bar],
    rule_results: list[RuleResult],
    meta: dict | None = None,
    symbol: str | None = None,
    watchlist: set[str] | None = None,
) -> dict:
    """Build the snapshot dict from the last bar, rule detail values, and
    per-symbol quote metadata (price_to_book/change_pct/in_watchlist)."""
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

    meta = meta or {}
    price_to_book = meta.get("price_to_book")
    snapshot["price_to_book"] = price_to_book if price_to_book is not None else float("inf")
    change_pct = meta.get("change_pct")
    snapshot["change_pct"] = change_pct if change_pct is not None else 0.0
    snapshot["in_watchlist"] = symbol in watchlist if (watchlist and symbol) else False
    industry = meta.get("industry") or ""
    snapshot["industry"] = industry
    snapshot["is_chip"] = "semiconductor" in industry.lower()

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
    watchlist = load_watchlist(settings.watchlist_path)
    cache = BarCache(_CACHE_PATH)
    provider = build_bar_provider(settings, cache)
    fundamentals_cache = FundamentalsCache(_FUNDAMENTALS_CACHE_PATH)
    fundamentals_provider = build_fundamentals_provider(settings, fundamentals_cache)
    engine = RuleEngine(rules_config.rules)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=_LOOKBACK_DAYS)

    bars, funda_snapshot = await asyncio.gather(
        provider.get_bars(symbol, start, end),
        fundamentals_provider.get_fundamentals(symbol),
    )
    if len(bars) < _MIN_BARS:
        print(f"[WARN] Only {len(bars)} bars for {symbol} — need at least {_MIN_BARS}")
        cache.close()
        fundamentals_cache.close()
        return

    meta = await build_quote_lookup_provider(settings, watchlist).quote_for(symbol)
    rule_results = engine.evaluate(symbol, bars, meta=meta, watchlist=watchlist)
    score = engine.score(rule_results)
    rules_passed = sum(1 for r in rule_results if r.passed)
    snapshot = _build_snapshot(bars, rule_results, meta=meta, symbol=symbol, watchlist=watchlist)

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
    report_path = write_ticker_report(
        signal,
        settings.alert_threshold,
        settings.universe,
        rule_descriptions={r.name: r.description for r in rules_config.rules},
    )
    print(f"[PDF] Report written to {report_path}")
    cache.close()
    fundamentals_cache.close()


async def run_backtest_cli(days: int, holding_days: int) -> None:
    """--backtest mode: run the historical backtest, write the PDF report,
    and print a short console summary. Orchestration only — all backtest
    logic lives in screener.backtest."""
    settings = get_settings()
    configure_logging(settings.log_level)

    result: BacktestResult = await run_backtest(days=days, holding_days=holding_days)
    report_path = write_backtest_report(result)

    if settings.database_url:
        try:
            await write_backtest_to_db(result, database_url=settings.database_url)
        except Exception as exc:
            logger.warning("db_write_failed", error=str(exc))

    delta_pp = result.avg_return_pct - result.baseline_avg_return_pct
    sign = "+" if delta_pp >= 0 else ""

    print(f"\n{'='*60}")
    print(f"  Backtest: {result.lookback_days} eval days, {result.holding_days}-day hold, "
          f"threshold {result.alert_threshold:.0%}")
    print(f"{'='*60}")
    print(f"  Signals fired : {result.total_signals}")
    print(f"  Win rate      : {result.win_rate:.2%} ({result.wins}W / {result.losses}L)")
    print(f"  Avg return    : {result.avg_return_pct:.2f}%")
    print(f"  Baseline      : {result.baseline_avg_return_pct:.2f}%")
    print(f"  Signal vs base: {sign}{delta_pp:.2f} pp")
    print(f"  [PDF] Report written to {report_path}")

    print(f"\n  Per-Rule Attribution:")
    print(f"  {'Rule':<24} {'Weight':>7}  {'Passed (n/win%/avg%)':<28} {'Failed (n/win%/avg%)':<28} {'Edge':>10}")
    print(f"  {'-'*100}")
    for a in result.rule_attribution:
        passed_str = f"{a.passed_count}/{a.passed_win_rate:.0%}/{a.passed_avg_return_pct:+.2f}%"
        failed_str = f"{a.failed_count}/{a.failed_win_rate:.0%}/{a.failed_avg_return_pct:+.2f}%"
        edge_str = f"{a.edge_pct:+.2f} pp"
        print(f"  {a.rule_name:<24} {a.weight:>7.1f}  {passed_str:<28} {failed_str:<28} {edge_str:>10}")
    print()


def run_auth_schwab() -> None:
    """--auth-schwab mode: run the one-time interactive Schwab OAuth
    authorization flow and exit. Pure orchestration — all OAuth logic lives
    in screener.data.schwab.auth."""
    settings = get_settings()
    configure_logging(settings.log_level)

    print("Opening browser for Schwab login...")
    auth = SchwabAuth(
        app_key=settings.schwab_app_key,
        app_secret=settings.schwab_app_secret,
        callback_url=settings.schwab_callback_url,
        token_path=settings.schwab_token_path,
    )
    auth.authorize()
    print(f"Schwab authorization complete. Token saved to {settings.schwab_token_path}")


def _start_scheduler() -> None:
    """Start the APScheduler cron loop (imported lazily to keep main.py clean)."""
    from screener.scheduler import start  # implemented in Step 9
    start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Stock screener")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true", help="Run once and exit")
    group.add_argument("--ticker", metavar="SYMBOL", help="Debug a single ticker")
    group.add_argument(
        "--backtest", action="store_true",
        help="Run a historical backtest of the current rules over the watchlist",
    )
    group.add_argument(
        "--auth-schwab", action="store_true",
        help="Run the one-time interactive Schwab OAuth authorization flow",
    )
    parser.add_argument(
        "--days", type=int, default=30,
        help="Backtest: number of trailing trading days to evaluate (default 30)",
    )
    parser.add_argument(
        "--hold", type=int, default=5,
        help="Backtest: holding period in trading days per simulated trade (default 5)",
    )
    args = parser.parse_args()

    if args.once:
        asyncio.run(run_screener())
    elif args.ticker:
        asyncio.run(run_ticker_debug(args.ticker.upper()))
    elif args.backtest:
        asyncio.run(run_backtest_cli(args.days, args.hold))
    elif args.auth_schwab:
        run_auth_schwab()
    else:
        _start_scheduler()


if __name__ == "__main__":
    main()
