"""All Pydantic v2 data models for the stock screener."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, field_validator


class Ticker(BaseModel):
    symbol: str
    name: str = ""
    sector: str = ""
    market_cap: float = 0.0


class Bar(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int

    @field_validator("timestamp")
    @classmethod
    def must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)


class RuleResult(BaseModel):
    rule_name: str
    passed: bool
    weight: float
    detail: dict[str, Any] = {}


class Signal(BaseModel):
    ticker: str
    timestamp: datetime
    score: float  # 0.0 – 1.0
    rules_passed: int
    rules_total: int
    rule_results: list[RuleResult]
    snapshot: dict[str, Any] = {}

    @field_validator("timestamp")
    @classmethod
    def must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    @field_validator("score")
    @classmethod
    def score_in_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"score must be between 0 and 1, got {v}")
        return v


class ScreenerRun(BaseModel):
    run_timestamp: datetime
    universe: str
    alert_threshold: float = 0.70
    signals: list[Signal] = []

    @field_validator("run_timestamp")
    @classmethod
    def must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)


class FundamentalsSnapshot(BaseModel):
    """Point-in-time snapshot of balance-sheet-quality metrics for a ticker.

    Used to hard-exclude weak-balance-sheet tickers before they reach the
    technical rule engine. Any metric that could not be computed (missing
    row, insufficient history, etc.) is `None` rather than NaN."""

    ticker: str
    as_of: datetime
    years_available: int
    fcf_5y_cumulative: float | None
    interest_coverage: float | None
    gross_margin: float | None
    ocf_ni_ratio: float | None
    net_margin: float | None
    share_dilution_5y: float | None

    @field_validator("as_of")
    @classmethod
    def must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)


class QualityGateResult(BaseModel):
    """Internal filtering type produced by the fundamentals quality gate.

    NOT part of the locked ScreenerRun/Signal JSON output schema documented
    in CLAUDE.md — must never be added to Signal or ScreenerRun. Exclusions
    surface only via structlog, never via the output schema."""

    ticker: str
    passed: bool
    failed_metrics: list[str] = []
    detail: dict[str, Any] = {}


class BacktestTrade(BaseModel):
    """A single simulated trade from the historical backtest: buy at the
    signal day's close, sell `holding_days` trading days later."""

    ticker: str
    signal_date: datetime
    score: float
    rules_passed: int
    rules_total: int
    buy_close: float
    sell_date: datetime
    sell_close: float
    return_pct: float
    win: bool

    @field_validator("signal_date", "sell_date")
    @classmethod
    def must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)


class BacktestResult(BaseModel):
    """Aggregate output of a historical backtest run. Not part of the
    locked ScreenerRun JSON schema — a separate, additive model."""

    universe: str
    holding_days: int
    alert_threshold: float
    lookback_days: int
    start_date: datetime
    end_date: datetime
    total_signals: int
    wins: int
    losses: int
    win_rate: float  # 0.0 - 1.0
    avg_return_pct: float
    total_return_pct: float
    best_trade_return_pct: float
    worst_trade_return_pct: float
    baseline_avg_return_pct: float
    trades: list[BacktestTrade] = []

    @field_validator("start_date", "end_date")
    @classmethod
    def must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)
