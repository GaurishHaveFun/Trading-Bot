"""Error hierarchy for the AI "why is this stock falling?" analysis engine.

Lives in its own module (rather than in `engine.py`) so that `engine.py` and
the provider modules under `screener.analysis.providers` can both import
these error classes without a circular import: `engine.py` builds provider
instances and providers raise these errors, so if the errors stayed defined
in `engine.py`, the providers would need to import `engine.py`, which itself
imports the providers -> circular import."""
from __future__ import annotations


class AnalysisError(Exception):
    """Base for all analysis-engine errors; route layer maps these to HTTP codes."""


class AnalysisUnavailable(AnalysisError):
    """Raised when the selected provider's API key is empty -- the feature is not configured."""


class AnalysisRefused(AnalysisError):
    """Raised when the model refuses to analyze this ticker (e.g. Anthropic's
    stop_reason == 'refusal', or a Gemini response blocked/filtered). Carries
    category/explanation describing the refusal when available."""

    def __init__(self, message: str, category: str | None = None, explanation: str | None = None) -> None:
        super().__init__(message)
        self.category = category
        self.explanation = explanation


class AnalysisTimeout(AnalysisError):
    """Raised when the call exceeds the 90s timeout."""


class AnalysisRateLimited(AnalysisError):
    """Raised on a provider-side rate-limit error (e.g. anthropic.RateLimitError,
    or a Gemini ClientError with .code == 429)."""


class AnalysisProviderError(AnalysisError):
    """Raised on any other API-side failure (e.g. anthropic.APIStatusError /
    anthropic.APIConnectionError, or a Gemini ClientError/ServerError/APIError
    that isn't a rate limit or auth failure)."""


class AnalysisParseError(AnalysisError):
    """Raised when the model's JSON output could not be parsed/validated into TickerAnalysis."""
