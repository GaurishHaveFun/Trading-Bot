"""Rule evaluation engine using asteval for safe expression evaluation."""
from __future__ import annotations

import re

import pandas as pd
from asteval import Interpreter

from screener.config import RuleConfig
from screener.indicators.library import latest_close, latest_volume
from screener.models import Bar, RuleResult
from screener.rules.functions import build_symbol_table
from screener.utils.logging import get_logger

logger = get_logger(__name__)


class RuleEngine:
    def __init__(self, rules: list[RuleConfig]) -> None:
        self._rules = rules

    def evaluate(self, symbol: str, bars: list[Bar]) -> list[RuleResult]:
        """Evaluate all rules for a ticker. Returns one RuleResult per rule."""
        df = _bars_to_df(bars)
        close = latest_close(df)
        volume = float(latest_volume(df))
        symbol_table = build_symbol_table(df, close, volume)

        results = []
        for rule in self._rules:
            result = self._eval_rule(rule, symbol_table, symbol)
            results.append(result)
        return results

    def _eval_rule(
        self,
        rule: RuleConfig,
        symbol_table: dict,
        symbol: str,
    ) -> RuleResult:
        """Evaluate a single rule condition via asteval."""
        aeval = Interpreter(symtable=dict(symbol_table))
        outcome = aeval(rule.condition)

        if aeval.error:
            errors = [str(e.get_error()) for e in aeval.error]
            logger.warning(
                "rule_eval_error",
                symbol=symbol,
                rule=rule.name,
                errors=errors,
            )
            return RuleResult(
                rule_name=rule.name,
                passed=False,
                weight=rule.weight,
                detail={"error": errors},
            )

        # asteval may return None for expressions that reference undefined names
        if outcome is None:
            logger.warning(
                "rule_eval_none",
                symbol=symbol,
                rule=rule.name,
                condition=rule.condition,
            )
            return RuleResult(
                rule_name=rule.name,
                passed=False,
                weight=rule.weight,
                detail={"error": ["expression evaluated to None"]},
            )

        passed = bool(outcome)

        # Capture the indicator values used in this rule for the detail dict
        detail = _extract_detail(rule.condition, symbol_table)

        return RuleResult(
            rule_name=rule.name,
            passed=passed,
            weight=rule.weight,
            detail=detail,
        )

    def score(self, results: list[RuleResult]) -> float:
        """Weighted score: sum(weight for passed) / sum(all weights). Returns 0.0 if no rules."""
        total = sum(r.weight for r in results)
        if total == 0:
            return 0.0
        passed = sum(r.weight for r in results if r.passed)
        return passed / total


def _bars_to_df(bars: list[Bar]) -> pd.DataFrame:
    """Convert list of Bar objects to a DataFrame sorted by timestamp."""
    records = [
        {
            "timestamp": b.timestamp,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
        }
        for b in bars
    ]
    df = pd.DataFrame(records)
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def _extract_detail(condition: str, symbol_table: dict) -> dict:
    """Extract scalar indicator values referenced in the condition string."""
    detail: dict = {}
    # Always include close and volume
    detail["close"] = symbol_table.get("close", 0.0)
    detail["volume"] = symbol_table.get("volume", 0.0)

    # Check which indicator functions are referenced by name
    for name in ("sma", "ema", "rsi", "atr", "sma_volume"):
        if name + "(" in condition:
            matches = re.findall(rf"{name}\((\d+)\)", condition)
            for period_str in matches:
                period = int(period_str)
                try:
                    fn = symbol_table[name]
                    val = fn(period)
                    key = f"{name}_{period}"
                    detail[key] = round(val, 4)
                except Exception:
                    pass
    return detail
