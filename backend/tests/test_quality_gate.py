"""Tests for the fundamentals quality gate."""
from datetime import datetime, timezone

from screener.config import QualityScreenConfig
from screener.models import FundamentalsSnapshot, QualityGateResult
from screener.rules.quality_gate import evaluate_quality_gate

CONFIG = QualityScreenConfig()


def _snapshot(**overrides) -> FundamentalsSnapshot:
    defaults = dict(
        ticker="TEST",
        as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
        years_available=5,
        fcf_5y_cumulative=1_000_000.0,
        interest_coverage=5.0,
        gross_margin=0.30,
        ocf_ni_ratio=0.9,
        net_margin=0.10,
        share_dilution_5y=0.05,
    )
    defaults.update(overrides)
    return FundamentalsSnapshot(**defaults)


def test_passes_everything():
    snapshot = _snapshot()
    result = evaluate_quality_gate(snapshot, CONFIG)
    assert isinstance(result, QualityGateResult)
    assert result.passed is True
    assert result.failed_metrics == []


def test_fails_single_metric():
    snapshot = _snapshot(gross_margin=0.05)  # below min_gross_margin (0.15)
    result = evaluate_quality_gate(snapshot, CONFIG)
    assert result.passed is False
    assert result.failed_metrics == ["gross_margin"]


def test_fails_multiple_metrics_lists_all():
    snapshot = _snapshot(
        gross_margin=0.05,  # fails
        net_margin=0.01,  # fails
        share_dilution_5y=0.50,  # fails (max is 0.20)
    )
    result = evaluate_quality_gate(snapshot, CONFIG)
    assert result.passed is False
    assert set(result.failed_metrics) == {"gross_margin", "net_margin", "share_dilution_5y"}
    assert len(result.failed_metrics) == 3


def test_none_interest_coverage_does_not_exclude():
    """A debt-free company has interest_coverage=None — this must never
    cause exclusion, since None always counts as passed for that check."""
    snapshot = _snapshot(interest_coverage=None)
    result = evaluate_quality_gate(snapshot, CONFIG)
    assert result.passed is True
    assert "interest_coverage" not in result.failed_metrics


def test_all_none_fields_pass():
    """years_available < 2 means every metric is None — nothing can be
    checked, so the ticker naturally passes."""
    snapshot = _snapshot(
        years_available=0,
        fcf_5y_cumulative=None,
        interest_coverage=None,
        gross_margin=None,
        ocf_ni_ratio=None,
        net_margin=None,
        share_dilution_5y=None,
    )
    result = evaluate_quality_gate(snapshot, CONFIG)
    assert result.passed is True
    assert result.failed_metrics == []
    assert result.detail == {}


def test_detail_only_contains_evaluated_metrics():
    snapshot = _snapshot(interest_coverage=None, share_dilution_5y=None)
    result = evaluate_quality_gate(snapshot, CONFIG)
    assert "interest_coverage" not in result.detail
    assert "min_interest_coverage" not in result.detail
    assert "share_dilution_5y" not in result.detail
    assert "max_share_dilution_5y" not in result.detail
    # the ones that were actually evaluated should be present
    assert result.detail["fcf_5y_cumulative"] == snapshot.fcf_5y_cumulative
    assert result.detail["min_fcf_5y_cumulative"] == CONFIG.min_fcf_5y_cumulative
    assert result.detail["gross_margin"] == snapshot.gross_margin
    assert result.detail["ocf_ni_ratio"] == snapshot.ocf_ni_ratio
    assert result.detail["net_margin"] == snapshot.net_margin
