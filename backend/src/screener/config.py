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
    schwab_app_key: str = ""
    schwab_app_secret: str = ""
    schwab_callback_url: str = "https://127.0.0.1:8182"
    schwab_token_path: str = ".cache/schwab_token.json"
    data_provider: str = "schwab"
    alert_threshold: float = 0.70
    log_level: str = "INFO"
    universe: str = "losers"  # "static" | "losers"
    watchlist_path: str = "config/watchlist.yaml"
    database_url: str = ""
    render_api_key: str = ""
    render_service_id: str = ""
    anthropic_api_key: str = ""
    analysis_model: str = "claude-opus-5"
    analysis_ttl_minutes: int = 360
    analysis_provider: str = "gemini"
    gemini_api_key: str = ""
    # Pinned to a dated model rather than the rolling "gemini-flash-latest"
    # alias: that alias can roll onto a newer model with no free-tier quota
    # (this happened -- see gemini_provider.py's 429 handling), silently
    # breaking the analysis feature. Override via GEMINI_MODEL if needed.
    gemini_model: str = "gemini-3.5-flash"
    # Off by default: Google Search grounding (the `google_search` tool) is
    # not free-tier eligible on Gemini -- attaching it returns
    # 429 RESOURCE_EXHAUSTED on every model tried, even when plain
    # generate_content calls succeed. Leaving this off keeps the
    # out-of-the-box config working. Set ANALYSIS_WEB_SEARCH=true if you're
    # on a paid Gemini plan, or if using the Anthropic provider, whose web
    # search tool has no such free-tier restriction.
    analysis_web_search: bool = False


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


def load_rules_config(path: str | Path) -> RulesConfig:
    """Parse rules.yaml into a RulesConfig model."""
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    return RulesConfig.model_validate(data)


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
