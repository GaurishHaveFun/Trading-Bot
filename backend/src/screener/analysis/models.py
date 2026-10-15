"""Pydantic v2 data models for the AI "why is this stock falling?" analysis
feature. Mirrors the field_validator UTC-coercion style used in
`screener.models`."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, field_validator


class NewsItem(BaseModel):
    title: str
    publisher: str | None
    url: str | None
    published_at: datetime | None
    summary: str | None

    @field_validator("published_at")
    @classmethod
    def must_be_utc(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return v
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)


class PeerMove(BaseModel):
    ticker: str
    change_pct: float | None
    sector: str | None


class AnalysisEvidence(BaseModel):
    ticker: str
    as_of: datetime
    profile: dict[str, Any]
    stats: dict[str, Any]
    targets: dict[str, Any]
    technicals: dict[str, Any]
    peers: list[PeerMove] = []
    news: list[NewsItem] = []

    @field_validator("as_of")
    @classmethod
    def must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)


class AnalysisDriver(BaseModel):
    title: str
    explanation: str
    evidence_type: Literal["news", "sector", "technical", "fundamental"]


class AnalysisSource(BaseModel):
    title: str
    url: str


class EvidenceBasis(BaseModel):
    """Machine-computed summary of what an analysis was actually built from
    -- counts of news items/peers, whether technicals were available, and
    whether web search was used. Always set in code (`providers/base.py`'s
    shared post-processing) after parsing the model's JSON response; never
    trust or accept a value the model itself tries to put here. Lets
    consumers (e.g. the frontend) distinguish "grounded in 10 headlines"
    from "worked from almost nothing." Optional with an all-default value so
    existing callers/serializers that predate this field keep working."""

    news_count: int = 0
    peers_count: int = 0
    has_technicals: bool = False
    web_search_used: bool = False


class TickerAnalysis(BaseModel):
    headline: str
    confidence: Literal["high", "medium", "low"]
    drivers: list[AnalysisDriver] = []
    sector_context: str | None
    what_to_watch: list[str] = []
    sources: list[AnalysisSource] = []
    evidence_basis: EvidenceBasis | None = None


class CachedAnalysis(BaseModel):
    ticker: str
    generated_at: datetime
    model: str
    change_pct: float | None
    analysis: TickerAnalysis

    @field_validator("generated_at")
    @classmethod
    def must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)
