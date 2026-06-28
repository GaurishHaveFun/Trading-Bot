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
