"""Unit tests for `screener.analysis.engine.analyze`'s factory/dispatch
contract. Provider-specific SDK call/error-mapping behavior now lives in
`test_analysis_providers.py`; this file only covers what's genuinely
`engine.py`'s own job: error classes remain importable as `engine.AnalysisX`
attributes, `analyze()` dispatches to the configured provider class, an
unknown provider name raises `ValueError`, and settings.analysis_model gets
mutated to the model that actually served the request on success."""
from datetime import datetime, timezone

import pytest

import screener.analysis.engine as engine_module
from screener.analysis.models import AnalysisEvidence, TickerAnalysis
from screener.config import Settings


def _sample_evidence():
    return AnalysisEvidence(
        ticker="TEST",
        as_of=datetime.now(timezone.utc),
        profile={"sector": "Technology"},
        stats={"price": 100.0},
        targets={},
        technicals={"rsi_14": 40.0},
        peers=[],
        news=[],
    )


def _sample_analysis():
    return TickerAnalysis(
        headline="Stock fell on sector-wide weakness",
        confidence="medium",
        drivers=[],
        sector_context="Broad tech selloff.",
        what_to_watch=[],
        sources=[],
    )


# --- error classes remain importable off engine.py ---

def test_error_classes_importable_from_engine():
    assert engine_module.AnalysisError is not None
    assert issubclass(engine_module.AnalysisUnavailable, engine_module.AnalysisError)
    assert issubclass(engine_module.AnalysisRefused, engine_module.AnalysisError)
    assert issubclass(engine_module.AnalysisTimeout, engine_module.AnalysisError)
    assert issubclass(engine_module.AnalysisRateLimited, engine_module.AnalysisError)
    assert issubclass(engine_module.AnalysisProviderError, engine_module.AnalysisError)
    assert issubclass(engine_module.AnalysisParseError, engine_module.AnalysisError)


# --- dispatch ---

async def test_analyze_dispatches_to_anthropic_provider(monkeypatch):
    called = {}

    async def _fake_generate(self, evidence):
        called["provider"] = self.name
        called["model"] = self.model
        return _sample_analysis()

    monkeypatch.setattr(engine_module.AnthropicProvider, "generate", _fake_generate)

    settings = Settings(anthropic_api_key="key", analysis_provider="anthropic")
    result = await engine_module.analyze(_sample_evidence(), settings=settings)

    assert called["provider"] == "anthropic"
    assert result.headline == "Stock fell on sector-wide weakness"
    assert settings.analysis_model == called["model"]


async def test_analyze_dispatches_to_gemini_provider(monkeypatch):
    called = {}

    async def _fake_generate(self, evidence):
        called["provider"] = self.name
        return _sample_analysis()

    monkeypatch.setattr(engine_module.GeminiProvider, "generate", _fake_generate)

    settings = Settings(gemini_api_key="key", analysis_provider="gemini")
    await engine_module.analyze(_sample_evidence(), settings=settings)

    assert called["provider"] == "gemini"


async def test_analyze_unknown_provider_raises_value_error():
    settings = Settings(analysis_provider="not-a-real-provider")

    with pytest.raises(ValueError):
        await engine_module.analyze(_sample_evidence(), settings=settings)


# --- settings.analysis_model mutation on success only ---

async def test_analyze_mutates_analysis_model_to_served_provider_on_success(monkeypatch):
    async def _fake_generate(self, evidence):
        return _sample_analysis()

    monkeypatch.setattr(engine_module.GeminiProvider, "generate", _fake_generate)

    settings = Settings(gemini_api_key="key", analysis_provider="gemini")
    assert settings.analysis_model != settings.gemini_model

    await engine_module.analyze(_sample_evidence(), settings=settings)

    assert settings.analysis_model == settings.gemini_model


async def test_analyze_does_not_mutate_analysis_model_on_failure(monkeypatch):
    async def _fake_generate(self, evidence):
        raise engine_module.AnalysisProviderError("boom")

    monkeypatch.setattr(engine_module.GeminiProvider, "generate", _fake_generate)

    settings = Settings(gemini_api_key="key", analysis_provider="gemini")
    original_model = settings.analysis_model

    with pytest.raises(engine_module.AnalysisProviderError):
        await engine_module.analyze(_sample_evidence(), settings=settings)

    assert settings.analysis_model == original_model
