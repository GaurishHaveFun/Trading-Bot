"""Tests for config loading."""
from pathlib import Path

import pytest

from screener.config import (
    QualityScreenConfig,
    RuleConfig,
    RulesConfig,
    load_quality_screen_config,
    load_rules_config,
    load_watchlist,
)

RULES_YAML = Path(__file__).parent.parent / "config" / "rules.yaml"
WATCHLIST_YAML = Path(__file__).parent.parent / "config" / "watchlist.yaml"
QUALITY_SCREEN_YAML = Path(__file__).parent.parent / "config" / "quality_screen.yaml"


def test_load_rules_config_parses_five_rules():
    config = load_rules_config(RULES_YAML)
    assert isinstance(config, RulesConfig)
    assert len(config.rules) == 5


def test_rule_names():
    config = load_rules_config(RULES_YAML)
    names = [r.name for r in config.rules]
    assert "big_tech_or_chip" in names
    assert "oversold_band" in names
    assert "quality_uptrend" in names
    assert "near_52w_low" in names
    assert "undervalued_pb" in names


def test_rule_weights():
    config = load_rules_config(RULES_YAML)
    weight_map = {r.name: r.weight for r in config.rules}
    assert weight_map["big_tech_or_chip"] == 2.0
    assert weight_map["oversold_band"] == 2.0
    assert weight_map["quality_uptrend"] == 1.5
    assert weight_map["near_52w_low"] == 1.0
    assert weight_map["undervalued_pb"] == 1.5


def test_schedule_parsed():
    config = load_rules_config(RULES_YAML)
    assert config.schedule.on == "0 16 * * 1-5"
    assert config.schedule.timezone == "America/New_York"


def test_rule_config_description_field():
    rule = RuleConfig(name="x", weight=1.0, condition="true", description="y")
    assert rule.description == "y"


def test_all_rules_have_descriptions():
    config = load_rules_config(RULES_YAML)
    for rule in config.rules:
        assert rule.description != ""


# --- watchlist ---

def test_load_watchlist_returns_set():
    watchlist = load_watchlist(WATCHLIST_YAML)
    assert isinstance(watchlist, set)


def test_load_watchlist_contains_expected():
    watchlist = load_watchlist(WATCHLIST_YAML)
    assert "AAPL" in watchlist
    assert "NVDA" in watchlist
    assert "SMCI" in watchlist


def test_load_watchlist_count():
    watchlist = load_watchlist(WATCHLIST_YAML)
    assert len(watchlist) == 16


# --- Settings ---

def test_settings_default_universe_is_losers():
    from screener.config import Settings
    settings = Settings(_env_file=None)
    assert settings.universe == "losers"


def test_settings_default_watchlist_path():
    from screener.config import Settings
    settings = Settings(_env_file=None)
    assert settings.watchlist_path == "config/watchlist.yaml"


# --- quality screen config ---

def test_load_quality_screen_config_parses():
    config = load_quality_screen_config(QUALITY_SCREEN_YAML)
    assert isinstance(config, QualityScreenConfig)
    assert config.min_fcf_5y_cumulative == 0.0
    assert config.min_interest_coverage == 2.0
    assert config.min_gross_margin == 0.15
    assert config.min_ocf_ni_ratio == 0.7
    assert config.min_net_margin == 0.05
    assert config.max_share_dilution_5y == 0.20
