"""Hard pass/fail quality gate over balance-sheet-quality fundamentals.

Fixed thresholds, no exemption/leniency carve-outs. A `None` metric (missing
or insufficient data) never causes exclusion — it's simply skipped, matching
the existing <200 bars skip-don't-fail convention used elsewhere in this
codebase.
"""
from __future__ import annotations

from screener.config import QualityScreenConfig
from screener.models import FundamentalsSnapshot, QualityGateResult
from screener.utils.logging import get_logger

logger = get_logger(__name__)


def evaluate_quality_gate(
    snapshot: FundamentalsSnapshot, config: QualityScreenConfig
) -> QualityGateResult:
    """Evaluate the 6 fixed-threshold checks against a FundamentalsSnapshot.

    Each check is only evaluated when its metric is not None; a None metric
    always counts as "passed" for that check. `passed` is True only if zero
    checks failed.
    """
    failed_metrics: list[str] = []
    detail: dict = {}

    if snapshot.fcf_5y_cumulative is not None:
        detail["fcf_5y_cumulative"] = snapshot.fcf_5y_cumulative
        detail["min_fcf_5y_cumulative"] = config.min_fcf_5y_cumulative
        if snapshot.fcf_5y_cumulative < config.min_fcf_5y_cumulative:
            failed_metrics.append("fcf_5y_cumulative")

    if snapshot.interest_coverage is not None:
        detail["interest_coverage"] = snapshot.interest_coverage
        detail["min_interest_coverage"] = config.min_interest_coverage
        if snapshot.interest_coverage < config.min_interest_coverage:
            failed_metrics.append("interest_coverage")

    if snapshot.gross_margin is not None:
        detail["gross_margin"] = snapshot.gross_margin
        detail["min_gross_margin"] = config.min_gross_margin
        if snapshot.gross_margin < config.min_gross_margin:
            failed_metrics.append("gross_margin")

    if snapshot.ocf_ni_ratio is not None:
        detail["ocf_ni_ratio"] = snapshot.ocf_ni_ratio
        detail["min_ocf_ni_ratio"] = config.min_ocf_ni_ratio
        if snapshot.ocf_ni_ratio < config.min_ocf_ni_ratio:
            failed_metrics.append("ocf_ni_ratio")

    if snapshot.net_margin is not None:
        detail["net_margin"] = snapshot.net_margin
        detail["min_net_margin"] = config.min_net_margin
        if snapshot.net_margin < config.min_net_margin:
            failed_metrics.append("net_margin")

    if snapshot.share_dilution_5y is not None:
        detail["share_dilution_5y"] = snapshot.share_dilution_5y
        detail["max_share_dilution_5y"] = config.max_share_dilution_5y
        if snapshot.share_dilution_5y > config.max_share_dilution_5y:
            failed_metrics.append("share_dilution_5y")

    passed = len(failed_metrics) == 0

    result = QualityGateResult(
        ticker=snapshot.ticker,
        passed=passed,
        failed_metrics=failed_metrics,
        detail=detail,
    )

    if not passed:
        logger.warning(
            "quality_gate_failed",
            symbol=snapshot.ticker,
            failed_metrics=failed_metrics,
            detail=detail,
        )

    return result
