"""AI "why is this stock falling?" analysis engine. Factory that selects a
concrete `AnalysisProvider` (Anthropic, Gemini, ...) per `settings.analysis_provider`
and dispatches to it. Provider-specific SDK calls, error mapping, prompts,
and schemas live under `screener.analysis.providers`; the error classes
themselves live in `screener.analysis.errors` (re-exported below so existing
importers like `screener.api.app`, which does
`from screener.analysis import engine as analysis_engine` and then catches
`analysis_engine.AnalysisUnavailable` etc., keep working unchanged)."""
from __future__ import annotations

from screener.analysis.errors import (
    AnalysisError,
    AnalysisParseError,
    AnalysisProviderError,
    AnalysisRateLimited,
    AnalysisRefused,
    AnalysisTimeout,
    AnalysisUnavailable,
)
from screener.analysis.models import AnalysisEvidence, TickerAnalysis
from screener.analysis.providers.anthropic_provider import AnthropicProvider
from screener.analysis.providers.base import AnalysisProvider
from screener.analysis.providers.gemini_provider import GeminiProvider
from screener.config import Settings, get_settings
from screener.utils.logging import get_logger

logger = get_logger(__name__)

_PROVIDERS: dict[str, type[AnalysisProvider]] = {
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
}


async def analyze(evidence: AnalysisEvidence, settings: Settings | None = None) -> TickerAnalysis:
    """Resolve the configured analysis provider and ask it to explain
    `evidence`'s ticker's price decline, returning the validated
    `TickerAnalysis`."""
    settings = settings or get_settings()

    provider_name = settings.analysis_provider.strip().lower()
    provider_cls = _PROVIDERS.get(provider_name)
    if provider_cls is None:
        valid = ", ".join(sorted(_PROVIDERS))
        raise ValueError(f"unknown analysis_provider {provider_name!r}; valid options: {valid}")

    provider = provider_cls(settings)
    logger.info(
        "analysis_provider_selected", provider=provider.name, model=provider.model, ticker=evidence.ticker
    )

    result = await provider.generate(evidence)

    # `screener.api.app` (which we cannot edit) unconditionally reports
    # `model=get_settings().analysis_model` in its API response immediately
    # after calling `analyze()` without ever passing `settings=` itself --
    # so it resolves the same `get_settings()` singleton `settings` resolves
    # to above. Mutating the singleton's `analysis_model` field to whichever
    # model actually just served this request (only on success, AFTER
    # `provider.generate()` returns without raising) is the only way,
    # without touching app.py, to make the API's reported `model` field
    # match the provider that actually ran -- e.g. so a Gemini-served
    # request reports "gemini-flash-latest" rather than the leftover
    # Anthropic default.
    settings.analysis_model = provider.model

    return result
