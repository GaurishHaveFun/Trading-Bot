"""Anthropic (Claude) implementation of `AnalysisProvider`. Sends the
assembled `AnalysisEvidence` to Claude, with web search enabled by default,
and validates the response into a `TickerAnalysis`."""
from __future__ import annotations

import asyncio

import anthropic

from screener.analysis.errors import (
    AnalysisParseError,
    AnalysisProviderError,
    AnalysisRateLimited,
    AnalysisRefused,
    AnalysisTimeout,
    AnalysisUnavailable,
)
from screener.analysis.models import AnalysisEvidence, TickerAnalysis
from screener.analysis.providers.base import (
    _SYSTEM_PROMPT,
    _TICKER_ANALYSIS_SCHEMA,
    AnalysisProvider,
    build_user_content,
    parse_analysis_json,
    postprocess_analysis,
)
from screener.config import Settings
from screener.utils.logging import get_logger

logger = get_logger(__name__)

_CALL_TIMEOUT_SECONDS = 90


class AnthropicProvider(AnalysisProvider):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def model(self) -> str:
        return self._settings.analysis_model

    async def generate(self, evidence: AnalysisEvidence) -> TickerAnalysis:
        settings = self._settings

        if not settings.anthropic_api_key:
            raise AnalysisUnavailable("ANTHROPIC_API_KEY is not configured")

        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

        user_content = build_user_content(evidence)

        create_kwargs = dict(
            model=settings.analysis_model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": _TICKER_ANALYSIS_SCHEMA},
            },
        )
        if settings.analysis_web_search:
            create_kwargs["tools"] = [
                {"type": "web_search_20260209", "name": "web_search", "max_uses": 4}
            ]

        # No ANTHROPIC_API_KEY was available in this dev environment to verify
        # structured outputs (`output_config.format`) compose correctly with the
        # `web_search_20260209` server tool in a live call. Per the Claude API
        # docs' "Using Both Together" example (json_schema format + tools), this
        # combination is documented to work, so it's used as the primary path;
        # parsing below is still defensive (fence-stripping, tolerant json.loads)
        # in case enforcement doesn't hold with a server tool present -- treat
        # this as unverified in production and check response.usage / a real
        # call before relying on it at scale.
        try:
            response = await asyncio.wait_for(
                client.messages.create(**create_kwargs),
                timeout=_CALL_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            logger.warning("analysis_timeout", ticker=evidence.ticker, provider=self.name)
            raise AnalysisTimeout(f"analysis timed out for {evidence.ticker}") from exc
        except anthropic.RateLimitError as exc:
            logger.warning(
                "analysis_rate_limited", ticker=evidence.ticker, provider=self.name, error=str(exc)
            )
            raise AnalysisRateLimited(str(exc)) from exc
        except anthropic.APIStatusError as exc:
            logger.warning(
                "analysis_provider_error",
                ticker=evidence.ticker,
                provider=self.name,
                status=exc.status_code,
                error=str(exc),
            )
            raise AnalysisProviderError(str(exc)) from exc
        except anthropic.APIConnectionError as exc:
            logger.warning(
                "analysis_connection_error", ticker=evidence.ticker, provider=self.name, error=str(exc)
            )
            raise AnalysisProviderError(str(exc)) from exc

        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None) if response.stop_details else None
            explanation = (
                getattr(response.stop_details, "explanation", None) if response.stop_details else None
            )
            logger.warning("analysis_refused", ticker=evidence.ticker, provider=self.name, category=category)
            raise AnalysisRefused(
                "model refused to analyze this ticker", category=category, explanation=explanation
            )

        try:
            text = next(block.text for block in response.content if block.type == "text")
        except StopIteration as exc:
            logger.warning("analysis_parse_error", ticker=evidence.ticker, error="no text block in response")
            raise AnalysisParseError("no text block in response") from exc

        analysis = parse_analysis_json(text, ticker=evidence.ticker)

        # Allowed source URLs: every NewsItem.url actually supplied as
        # evidence, plus -- when web search was used -- the URLs Claude's
        # web_search server tool actually returned this call (from
        # `web_search_tool_result` content blocks). Anything else in the
        # model's `sources` is dropped in `postprocess_analysis` below.
        allowed_urls: set[str] = {n.url for n in evidence.news if n.url}
        if settings.analysis_web_search:
            for block in response.content:
                if getattr(block, "type", None) != "web_search_tool_result":
                    continue
                content = getattr(block, "content", None)
                if not isinstance(content, list):
                    continue
                for item in content:
                    url = getattr(item, "url", None)
                    if url:
                        allowed_urls.add(url)

        return postprocess_analysis(
            analysis,
            evidence,
            allowed_source_urls=allowed_urls,
            web_search_used=settings.analysis_web_search,
        )
