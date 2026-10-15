"""Google Gemini implementation of `AnalysisProvider`. Sends the assembled
`AnalysisEvidence` to Gemini (via `google-genai`'s async client), with
Google Search grounding enabled by default, and validates the response into
a `TickerAnalysis`.

Structured output (`response_schema`) and Google Search grounding
(`google_search` tool) are NOT combined in the same request -- see the
build-plan notes this module was written against: despite docs claiming
Gemini 3+ supports combining them, there's a credible open bug report
(googleapis/python-genai #665) and forum reports of grounding metadata
coming back empty, or an outright unsupported-combination error, when both
are set together. So: web search on -> prompt-embedded JSON instructions +
tolerant text parsing (reusing `parse_analysis_json`); web search off ->
`response_schema` structured output, since there's no tool to conflict
with."""
from __future__ import annotations

import asyncio

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from screener.analysis.errors import (
    AnalysisProviderError,
    AnalysisRateLimited,
    AnalysisRefused,
    AnalysisTimeout,
    AnalysisUnavailable,
)
from screener.analysis.models import AnalysisEvidence, AnalysisSource, TickerAnalysis
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

# Blocked/refused finish reasons worth surfacing distinctly in logs/errors --
# anything else unexpected still falls into the generic "no usable text"
# AnalysisRefused branch below.
_REFUSAL_FINISH_REASONS = {"SAFETY", "PROHIBITED_CONTENT", "RECITATION", "BLOCKLIST", "OTHER"}


def _to_gemini_schema(schema: dict) -> dict:
    """Recursively convert a standard-JSON-Schema dict (the dialect
    `_TICKER_ANALYSIS_SCHEMA` in `base.py` is written in, and which the
    Anthropic provider passes through unchanged) into the dialect Gemini's
    `google.genai.types.Schema` accepts.

    The two dialects diverge in ways that make the shared schema unusable
    as-is against Gemini:
    - `"type": ["X", "null"]` (a standard-JSON-Schema nullable union) isn't
      a valid `types.Schema.type` -- Gemini wants a single type plus
      `"nullable": True`.
    - `types.Schema.type` values are uppercase enum members
      ("STRING", "OBJECT", ...), not lowercase JSON Schema type names.
    - `additionalProperties` has no equivalent on `types.Schema` and is
      dropped.

    `properties` and `items` are recursed into so nested objects/arrays
    (e.g. `drivers`/`sources`, which are arrays of objects, and
    `drivers[].evidence_type`, an enum) convert correctly. `required`,
    `enum`, and `description` pass through unchanged. This is intentionally
    derived from the shared schema rather than a hand-maintained copy, so
    the two can't drift apart.
    """
    result: dict = {}

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        non_null_types = [t for t in schema_type if t != "null"]
        if len(non_null_types) != 1:
            raise ValueError(f"unsupported type union for Gemini schema conversion: {schema_type!r}")
        result["type"] = non_null_types[0].upper()
        if "null" in schema_type:
            result["nullable"] = True
    elif isinstance(schema_type, str):
        result["type"] = schema_type.upper()

    if "enum" in schema:
        result["enum"] = schema["enum"]
    if "description" in schema:
        result["description"] = schema["description"]
    if "required" in schema:
        result["required"] = schema["required"]
    if "properties" in schema:
        result["properties"] = {
            key: _to_gemini_schema(value) for key, value in schema["properties"].items()
        }
    if "items" in schema:
        result["items"] = _to_gemini_schema(schema["items"])

    return result


_GEMINI_TICKER_ANALYSIS_SCHEMA = _to_gemini_schema(_TICKER_ANALYSIS_SCHEMA)


def _schema_prompt_suffix() -> str:
    import json

    return (
        "\n\nRespond with ONLY a single JSON object matching exactly this JSON "
        f"schema, with no prose, no markdown fences, and no commentary outside "
        f"the JSON object:\n{json.dumps(_TICKER_ANALYSIS_SCHEMA, indent=2)}"
    )


class GeminiProvider(AnalysisProvider):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def model(self) -> str:
        return self._settings.gemini_model

    async def generate(self, evidence: AnalysisEvidence) -> TickerAnalysis:
        settings = self._settings

        if not settings.gemini_api_key:
            raise AnalysisUnavailable("GEMINI_API_KEY is not configured")

        client = genai.Client(api_key=settings.gemini_api_key)

        user_content = build_user_content(evidence)
        web_search = settings.analysis_web_search

        if web_search:
            grounding_tool = types.Tool(google_search=types.GoogleSearch())
            config = types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                tools=[grounding_tool],
            )
            contents = user_content + _schema_prompt_suffix()
        else:
            config = types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=_GEMINI_TICKER_ANALYSIS_SCHEMA,
            )
            contents = user_content

        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=settings.gemini_model,
                    contents=contents,
                    config=config,
                ),
                timeout=_CALL_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            logger.warning("analysis_timeout", ticker=evidence.ticker, provider=self.name)
            raise AnalysisTimeout(f"analysis timed out for {evidence.ticker}") from exc
        except genai_errors.ClientError as exc:
            code = getattr(exc, "code", None)
            if code == 429:
                if web_search:
                    # Verified against the live API: attaching the
                    # google_search grounding tool returns 429
                    # RESOURCE_EXHAUSTED on every model tried, on this
                    # free-tier key, even though plain (non-grounded) calls
                    # succeed. A generic "rate limited" log would send
                    # someone chasing request-volume theories instead of the
                    # actual cause, so this gets its own event.
                    logger.warning(
                        "analysis_grounding_quota_exhausted",
                        ticker=evidence.ticker,
                        provider=self.name,
                        error=str(exc),
                        hint=(
                            "Google Search grounding is likely not available on the "
                            "Gemini free tier for this account; set ANALYSIS_WEB_SEARCH=false"
                        ),
                    )
                else:
                    logger.warning(
                        "analysis_rate_limited", ticker=evidence.ticker, provider=self.name, error=str(exc)
                    )
                raise AnalysisRateLimited(str(exc)) from exc
            if code in (401, 403):
                logger.warning(
                    "analysis_unavailable", ticker=evidence.ticker, provider=self.name, error=str(exc)
                )
                raise AnalysisUnavailable(str(exc)) from exc
            logger.warning(
                "analysis_provider_error", ticker=evidence.ticker, provider=self.name, code=code, error=str(exc)
            )
            raise AnalysisProviderError(str(exc)) from exc
        except genai_errors.ServerError as exc:
            logger.warning(
                "analysis_provider_error", ticker=evidence.ticker, provider=self.name, error=str(exc)
            )
            raise AnalysisProviderError(str(exc)) from exc
        except genai_errors.APIError as exc:
            logger.warning(
                "analysis_provider_error", ticker=evidence.ticker, provider=self.name, error=str(exc)
            )
            raise AnalysisProviderError(str(exc)) from exc

        # A blocked prompt (prompt_feedback.block_reason) or a blocked/absent
        # candidate is treated as a refusal, not a parse error -- there's
        # nothing to parse, and the model declined to produce output rather
        # than producing malformed output.
        block_reason = getattr(getattr(response, "prompt_feedback", None), "block_reason", None)
        if block_reason:
            logger.warning("analysis_refused", ticker=evidence.ticker, provider=self.name, category=str(block_reason))
            raise AnalysisRefused("model refused to analyze this ticker", category=str(block_reason))

        candidates = getattr(response, "candidates", None) or []
        if candidates:
            finish_reason = getattr(candidates[0], "finish_reason", None)
            finish_reason_str = str(finish_reason) if finish_reason is not None else None
            if finish_reason_str and any(r in finish_reason_str for r in _REFUSAL_FINISH_REASONS):
                logger.warning(
                    "analysis_refused", ticker=evidence.ticker, provider=self.name, category=finish_reason_str
                )
                raise AnalysisRefused("model refused to analyze this ticker", category=finish_reason_str)

        text = getattr(response, "text", None)
        if not text or not text.strip():
            logger.warning("analysis_refused", ticker=evidence.ticker, provider=self.name, category="empty_response")
            raise AnalysisRefused("model returned no text to analyze this ticker", category="empty_response")

        analysis = parse_analysis_json(text, ticker=evidence.ticker)

        # Grounding sources returned by Google Search grounding this call --
        # used both to backfill an empty `sources` list (existing behavior)
        # and, unconditionally, as part of the allowed-URL set that
        # `postprocess_analysis` filters the model's `sources` against.
        grounding_sources: list[AnalysisSource] = []
        if web_search and candidates:
            grounding_metadata = getattr(candidates[0], "grounding_metadata", None)
            chunks = getattr(grounding_metadata, "grounding_chunks", None) or []
            for chunk in chunks:
                web = getattr(chunk, "web", None)
                if web is None:
                    continue
                uri = getattr(web, "uri", None)
                if not uri:
                    continue
                title = getattr(web, "title", None) or uri
                grounding_sources.append(AnalysisSource(title=title, url=uri))
            if grounding_sources and not analysis.sources:
                analysis = analysis.model_copy(update={"sources": grounding_sources})

        allowed_urls: set[str] = {n.url for n in evidence.news if n.url}
        allowed_urls.update(source.url for source in grounding_sources)

        analysis = postprocess_analysis(
            analysis,
            evidence,
            allowed_source_urls=allowed_urls,
            web_search_used=web_search,
        )

        logger.info("analysis_generated", ticker=evidence.ticker, provider=self.name, model=self.model)
        return analysis
