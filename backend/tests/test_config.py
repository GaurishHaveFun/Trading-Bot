"""Tests for config loading."""
from pathlib import Path

import pytest

from screener.config import RulesConfig, load_rules_config

RULES_YAML = Path(__file__).parent.parent / "config" / "rules.yaml"


def test_load_rules_config_parses_five_rules():
    config = load_rules_config(RULES_YAML)
    assert isinstance(config, RulesConfig)
    assert len(config.rules) == 5


def test_rule_names():
    config = load_rules_config(RULES_YAML)
    names = [r.name for r in config.rules]
    assert "oversold_rsi" in names
    assert "above_long_trend" in names
    assert "golden_cross_state" in names
    assert "volume_spike" in names
    assert "reasonable_volatility" in names


def test_rule_weights():
    config = load_rules_config(RULES_YAML)
    weight_map = {r.name: r.weight for r in config.rules}
    assert weight_map["oversold_rsi"] == 2.0
    assert weight_map["above_long_trend"] == 1.5


def test_schedule_parsed():
    config = load_rules_config(RULES_YAML)
    assert config.schedule.on == "0 16 * * 1-5"
    assert config.schedule.timezone == "America/New_York"
