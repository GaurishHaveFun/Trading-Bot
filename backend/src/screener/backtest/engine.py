"""Historical backtest of the screener's rules over a window of past trading days.

Answers: "over the last N trading days, which watchlist stocks would have
passed the current rules, and would buying on the signal day have been
profitable?" This is purely additive — it never touches the locked
ScreenerRun JSON schema or the live scheduler pipeline.

Design constraints (see backend/docs/backtest.md for the full rationale):
- Universe is the static watchlist only (config/watchlist.yaml) — the live
  "losers" universe can only be reconstructed for *today*, not historically.
- The `undervalued_pb` rule is dropped by the caller before constructing the
  RuleEngine passed into evaluate_symbol/run_backtest: price-to-book is a
  live-quote scalar with no historical daily series. RuleEngine.score()
  already divides by the total weight of whatever rules it was built with,
  so scoring on the remaining 4 rules automatically rescales the
  denominator — no new scoring math is needed here.
- Exit rule is a fixed holding_days-trading-day hold: buy at the signal
  day's close, sell at the close `holding_days` bars later.
- No look-ahead: evaluating day i only ever sees bars[:i+1].
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from screener.config import RuleConfig, get_settings, load_rules_config, load_watchlist
from screener.data import BarCache
from screener.data.factory import build_bar_provider
from screener.models import Bar, BacktestResult, BacktestTrade, RuleAttribution
from screener.rules import RuleEngine
from screener.utils.logging import get_logger

logger = get_logger(__name__)

_RULES_PATH = Path("config/rules.yaml")
_CACHE_PATH = Path(".cache/bars.db")
_MIN_BARS = 200
_CONCURRENCY = 10
_DROPPED_RULE = "undervalued_pb"

# 200 trailing bars for indicators + up to ~30 eval days + a few holding-days
# forward window + weekends/holidays slack. 500 calendar days comfortably
# covers ~250-260 trading days, which is enough headroom for the default
# 30-day/5-day-hold backtest (and most reasonable overrides of --days/--hold).
_LOOKBACK_DAYS = 500


def evaluate_symbol(
    symbol: str,
    bars: list[Bar],
    engine: RuleEngine,
    watchlist: set[str],
    threshold: float,
    holding_days: int,
    eval_days: int,
) -> tuple[list[BacktestTrade], list[float], dict[str, list[tuple[bool, float]]]]:
    """Pure, offline-testable core: walk the last `eval_days` valid evaluation
    points in `bars` (ascending order) and simulate a fixed-hold trade for
    every day the rules score at/above `threshold`.

    An evaluation day i is valid when both:
      - i >= _MIN_BARS - 1 (enough trailing history for indicators), and
      - i + holding_days < len(bars) (enough forward bars for the exit).

    Returns (trades, all_forward_returns, rule_evaluations):
      - trades: one BacktestTrade per day the composite score cleared
        `threshold`.
      - all_forward_returns: the holding_days-forward return for EVERY valid
        evaluation day regardless of whether a signal fired, used by the
        caller to compute the baseline "did the rules add edge over just
        holding anything" comparison.
      - rule_evaluations: dict mapping rule_name -> [(passed, forward_return), ...],
        with one entry per valid evaluation day per rule (the same
        forward_return value for that day is paired with every rule's
        individual pass/fail outcome), used to compute per-rule return
        attribution independent of whether the composite score fired.
    """
    n = len(bars)
    valid_indices = [i for i in range(n) if i >= _MIN_BARS - 1 and i + holding_days < n]
    eval_indices = valid_indices[-eval_days:] if eval_days > 0 else []

    trades: list[BacktestTrade] = []
    forward_returns: list[float] = []
    rule_evaluations: dict[str, list[tuple[bool, float]]] = {}

    for i in eval_indices:
        buy_bar = bars[i]
        sell_bar = bars[i + holding_days]
        forward_return = (sell_bar.close - buy_bar.close) / buy_bar.close * 100
        forward_returns.append(forward_return)

        # No look-ahead: only bars up to and including day i are visible.
        results = engine.evaluate(symbol, bars[: i + 1], meta=None, watchlist=watchlist)
        score = engine.score(results)

        for r in results:
            rule_evaluations.setdefault(r.rule_name, []).append((r.passed, forward_return))

        if score >= threshold:
            rules_passed = sum(1 for r in results if r.passed)
            trades.append(
                BacktestTrade(
                    ticker=symbol,
                    signal_date=buy_bar.timestamp,
                    score=score,
                    rules_passed=rules_passed,
                    rules_total=len(results),
                    buy_close=buy_bar.close,
                    sell_date=sell_bar.timestamp,
                    sell_close=sell_bar.close,
                    return_pct=forward_return,
                    win=forward_return > 0,
                )
            )

    return trades, forward_returns, rule_evaluations


def _aggregate_rule_attribution(
    rule_configs: list[RuleConfig],
    rule_evaluations: dict[str, list[tuple[bool, float]]],
) -> list[RuleAttribution]:
    """Pure, offline-testable aggregation: for each rule config (in the given
    order, which determines display order downstream), split its recorded
    (passed, forward_return) observations into passed/failed groups and
    compute win rate + average forward return per side. A win is
    forward_return > 0 (matching BacktestTrade.win's definition). If either
    side has zero observations, that side's win_rate/avg_return_pct is 0.0
    and edge_pct is 0.0 (see RuleAttribution's docstring)."""
    attributions: list[RuleAttribution] = []
    for rule in rule_configs:
        observations = rule_evaluations.get(rule.name, [])
        passed_returns = [r for passed, r in observations if passed]
        failed_returns = [r for passed, r in observations if not passed]

        passed_count = len(passed_returns)
        failed_count = len(failed_returns)

        if passed_count:
            passed_win_rate = sum(1 for r in passed_returns if r > 0) / passed_count
            passed_avg_return_pct = sum(passed_returns) / passed_count
        else:
            passed_win_rate = 0.0
            passed_avg_return_pct = 0.0

        if failed_count:
            failed_win_rate = sum(1 for r in failed_returns if r > 0) / failed_count
            failed_avg_return_pct = sum(failed_returns) / failed_count
        else:
            failed_win_rate = 0.0
            failed_avg_return_pct = 0.0

        edge_pct = (
            passed_avg_return_pct - failed_avg_return_pct
            if passed_count and failed_count
            else 0.0
        )

        attributions.append(
            RuleAttribution(
                rule_name=rule.name,
                weight=rule.weight,
                passed_count=passed_count,
                passed_win_rate=passed_win_rate,
                passed_avg_return_pct=passed_avg_return_pct,
                failed_count=failed_count,
                failed_win_rate=failed_win_rate,
                failed_avg_return_pct=failed_avg_return_pct,
                edge_pct=edge_pct,
            )
        )
    return attributions


async def run_backtest(days: int = 30, holding_days: int = 5) -> BacktestResult:
    """Orchestration: fetch bars for the watchlist, evaluate each symbol's
    trailing `days` trading days against the current rules (minus
    `undervalued_pb`), and aggregate into a BacktestResult."""
    settings = get_settings()
    rules_config = load_rules_config(_RULES_PATH)
    watchlist = load_watchlist(settings.watchlist_path)

    filtered_rules = [r for r in rules_config.rules if r.name != _DROPPED_RULE]
    engine = RuleEngine(filtered_rules)
    total_weight = sum(r.weight for r in filtered_rules)

    cache = BarCache(_CACHE_PATH)
    provider = build_bar_provider(settings, cache)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=_LOOKBACK_DAYS)

    logger.info(
        "backtest_start",
        symbols=len(watchlist),
        days=days,
        holding_days=holding_days,
        rules=len(filtered_rules),
        total_weight=total_weight,
        start=str(start.date()),
        end=str(end.date()),
    )

    sem = asyncio.Semaphore(_CONCURRENCY)

    async def fetch_one(symbol: str) -> tuple[str, list[Bar]]:
        async with sem:
            bars = await provider.get_bars(symbol, start, end)
            return symbol, bars

    results = await asyncio.gather(*[fetch_one(s) for s in sorted(watchlist)], return_exceptions=True)

    all_trades: list[BacktestTrade] = []
    all_forward_returns: list[float] = []
    combined_rule_evaluations: dict[str, list[tuple[bool, float]]] = {}

    for item in results:
        if isinstance(item, Exception):
            logger.warning("fetch_error", error=str(item))
            continue
        symbol, bars = item
        if len(bars) < _MIN_BARS:
            logger.warning("insufficient_bars", symbol=symbol, count=len(bars))
            continue

        trades, forward_returns, rule_evaluations = evaluate_symbol(
            symbol,
            bars,
            engine,
            watchlist,
            settings.alert_threshold,
            holding_days,
            days,
        )
        all_trades.extend(trades)
        all_forward_returns.extend(forward_returns)
        for rule_name, observations in rule_evaluations.items():
            combined_rule_evaluations.setdefault(rule_name, []).extend(observations)

    cache.close()

    all_trades.sort(key=lambda t: t.return_pct, reverse=True)

    total_signals = len(all_trades)
    wins = sum(1 for t in all_trades if t.win)
    losses = total_signals - wins
    win_rate = wins / total_signals if total_signals else 0.0
    avg_return_pct = sum(t.return_pct for t in all_trades) / total_signals if total_signals else 0.0
    total_return_pct = sum(t.return_pct for t in all_trades)
    best_trade_return_pct = max((t.return_pct for t in all_trades), default=0.0)
    worst_trade_return_pct = min((t.return_pct for t in all_trades), default=0.0)
    baseline_avg_return_pct = (
        sum(all_forward_returns) / len(all_forward_returns) if all_forward_returns else 0.0
    )
    rule_attribution = _aggregate_rule_attribution(filtered_rules, combined_rule_evaluations)

    result = BacktestResult(
        universe="watchlist",
        holding_days=holding_days,
        alert_threshold=settings.alert_threshold,
        lookback_days=days,
        start_date=start,
        end_date=end,
        total_signals=total_signals,
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        avg_return_pct=avg_return_pct,
        total_return_pct=total_return_pct,
        best_trade_return_pct=best_trade_return_pct,
        worst_trade_return_pct=worst_trade_return_pct,
        baseline_avg_return_pct=baseline_avg_return_pct,
        trades=all_trades,
        rule_attribution=rule_attribution,
    )

    logger.info(
        "backtest_complete",
        total_signals=total_signals,
        win_rate=round(win_rate, 4),
        avg_return_pct=round(avg_return_pct, 4),
        baseline_avg_return_pct=round(baseline_avg_return_pct, 4),
    )

    return result
