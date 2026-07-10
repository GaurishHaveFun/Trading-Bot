"""Configuration loading: pydantic-settings for env vars and YAML rules config."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    finnhub_api_key: str = ""
    alpaca_key: str = ""
    alpaca_secret: str = ""
    alert_threshold: float = 0.70
    log_level: str = "INFO"
    universe: str = "losers"  # "static" | "losers"
    watchlist_path: str = "config/watchlist.yaml"


class RuleConfig(BaseModel):
    name: str
    weight: float
    condition: str
    description: str = ""


class ScheduleConfig(BaseModel):
    on: str
    timezone: str = "America/New_York"


class RulesConfig(BaseModel):
    schedule: ScheduleConfig
    rules: list[RuleConfig]


class QualityScreenConfig(BaseModel):
    min_fcf_5y_cumulative: float = 0.0
    min_interest_coverage: float = 2.0
    min_gross_margin: float = 0.15
    min_ocf_ni_ratio: float = 0.7
    min_net_margin: float = 0.05
    max_share_dilution_5y: float = 0.20


def load_rules_config(path: str | Path) -> RulesConfig:
    """Parse rules.yaml into a RulesConfig model."""
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    return RulesConfig.model_validate(data)


def load_quality_screen_config(path: str | Path) -> QualityScreenConfig:
    """Parse quality_screen.yaml into a QualityScreenConfig model."""
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    return QualityScreenConfig.model_validate(data)


def load_watchlist(path: str | Path) -> set[str]:
    """Parse watchlist.yaml into a set of ticker symbols."""
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    return {str(s) for s in data["symbols"]}


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
