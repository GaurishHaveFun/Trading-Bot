"""Unit tests for the concrete analysis providers (Anthropic, Gemini) and
the shared JSON-parsing helper in `screener.analysis.providers.base`. No
network access -- `anthropic.AsyncAnthropic` and `google.genai.Client` are
both monkeypatched with fakes shaped like the real SDKs."""
import asyncio
import json
from datetime import datetime, timezone

import pytest

import screener.analysis.engine as engine_module
import screener.analysis.providers.anthropic_provider as anthropic_provider_module
import screener.analysis.providers.gemini_provider as gemini_provider_module
from screener.analysis.errors import (
    AnalysisParseError,
    AnalysisProviderError,
    AnalysisRateLimited,
    AnalysisRefused,
    AnalysisTimeout,
    AnalysisUnavailable,
)
from screener.analysis.models import AnalysisEvidence, NewsItem, PeerMove
from screener.analysis.providers.anthropic_provider import AnthropicProvider
from screener.analysis.providers.base import (
    _SYSTEM_PROMPT,
    _TICKER_ANALYSIS_SCHEMA,
    build_user_content,
    normalize_url,
    parse_analysis_json,
    postprocess_analysis,
)
from screener.analysis.providers.gemini_provider import GeminiProvider
from screener.config import Settings
from google.genai import errors as genai_errors

import anthropic


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


_VALID_ANALYSIS_JSON = json.dumps(
    {
        "headline": "Stock fell on sector-wide weakness",
        "confidence": "medium",
        "drivers": [
            {
                "title": "Sector drift",
                "explanation": "Peers in the same sector also declined today.",
                "evidence_type": "sector",
            }
        ],
        "sector_context": "Broad tech selloff.",
        "what_to_watch": ["Next earnings report"],
        "sources": [],
    }
)


# ============================================================
# shared parse_analysis_json (providers/base.py)
# ============================================================

def test_parse_analysis_json_valid():
    result = parse_analysis_json(_VALID_ANALYSIS_JSON, ticker="TEST")
    assert result.headline == "Stock fell on sector-wide weakness"


def test_parse_analysis_json_strips_fences():
    fenced = f"```json\n{_VALID_ANALYSIS_JSON}\n```"
    result = parse_analysis_json(fenced, ticker="TEST")
    assert result.confidence == "medium"


def test_parse_analysis_json_malformed_raises_parse_error():
    with pytest.raises(AnalysisParseError):
        parse_analysis_json("not valid json{{{", ticker="TEST")


def test_parse_analysis_json_schema_mismatch_raises_parse_error():
    with pytest.raises(AnalysisParseError):
        parse_analysis_json(json.dumps({"headline": "x"}), ticker="TEST")


# ============================================================
# provider selection via engine.analyze()
# ============================================================

async def test_engine_selects_anthropic_provider(monkeypatch):
    class _FakeMessages:
        async def create(self, **kwargs):
            return _FakeAnthropicResponse(content=[_FakeTextBlock(_VALID_ANALYSIS_JSON)])

    class _FakeClient:
        def __init__(self, **kwargs):
            self.messages = _FakeMessages()

    monkeypatch.setattr(anthropic_provider_module.anthropic, "AsyncAnthropic", _FakeClient)

    settings = Settings(anthropic_api_key="key", analysis_provider="anthropic")
    result = await engine_module.analyze(_sample_evidence(), settings=settings)
    assert result.headline == "Stock fell on sector-wide weakness"


async def test_engine_selects_gemini_provider(monkeypatch):
    _patch_gemini_client(monkeypatch, _fake_gemini_response())

    settings = Settings(gemini_api_key="key", analysis_provider="gemini")
    result = await engine_module.analyze(_sample_evidence(), settings=settings)
    assert result.headline == "Stock fell on sector-wide weakness"


async def test_engine_unknown_provider_raises_value_error():
    settings = Settings(analysis_provider="bogus")
    with pytest.raises(ValueError):
        await engine_module.analyze(_sample_evidence(), settings=settings)


# ============================================================
# Anthropic provider: fakes
# ============================================================

class _FakeTextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _FakeStopDetails:
    def __init__(self, category=None, explanation=None):
        self.category = category
        self.explanation = explanation


class _FakeAnthropicResponse:
    def __init__(self, content, stop_reason="end_turn", stop_details=None):
        self.content = content
        self.stop_reason = stop_reason
        self.stop_details = stop_details


class _FakeAnthropicMessages:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.last_kwargs = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self._exc is not None:
            raise self._exc
        return self._response


class _FakeAnthropicClient:
    def __init__(self, response=None, exc=None, **kwargs):
        self.messages = _FakeAnthropicMessages(response=response, exc=exc)


def _settings_anthropic(key="test-key", web_search=True):
    return Settings(anthropic_api_key=key, analysis_provider="anthropic", analysis_web_search=web_search)


# --- Anthropic error mapping ---

async def test_anthropic_rate_limited(monkeypatch):
    exc = anthropic.RateLimitError("rate limited", response=_fake_httpx_response(429), body=None)
    fake_client = _FakeAnthropicClient(exc=exc)
    monkeypatch.setattr(anthropic_provider_module.anthropic, "AsyncAnthropic", lambda **kw: fake_client)

    provider = AnthropicProvider(_settings_anthropic())
    with pytest.raises(AnalysisRateLimited):
        await provider.generate(_sample_evidence())


async def test_anthropic_api_status_error_maps_to_provider_error(monkeypatch):
    exc = anthropic.APIStatusError("server error", response=_fake_httpx_response(500), body=None)
    fake_client = _FakeAnthropicClient(exc=exc)
    monkeypatch.setattr(anthropic_provider_module.anthropic, "AsyncAnthropic", lambda **kw: fake_client)

    provider = AnthropicProvider(_settings_anthropic())
    with pytest.raises(AnalysisProviderError):
        await provider.generate(_sample_evidence())


async def test_anthropic_timeout(monkeypatch):
    fake_client = _FakeAnthropicClient(response=_FakeAnthropicResponse(content=[]))
    monkeypatch.setattr(anthropic_provider_module.anthropic, "AsyncAnthropic", lambda **kw: fake_client)

    async def _raise_timeout(*args, **kwargs):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(anthropic_provider_module.asyncio, "wait_for", _raise_timeout)

    provider = AnthropicProvider(_settings_anthropic())
    with pytest.raises(AnalysisTimeout):
        await provider.generate(_sample_evidence())


async def test_anthropic_refused(monkeypatch):
    fake_response = _FakeAnthropicResponse(
        content=[],
        stop_reason="refusal",
        stop_details=_FakeStopDetails(category="cyber", explanation="policy decline"),
    )
    fake_client = _FakeAnthropicClient(response=fake_response)
    monkeypatch.setattr(anthropic_provider_module.anthropic, "AsyncAnthropic", lambda **kw: fake_client)

    provider = AnthropicProvider(_settings_anthropic())
    with pytest.raises(AnalysisRefused) as exc_info:
        await provider.generate(_sample_evidence())
    assert exc_info.value.category == "cyber"


async def test_anthropic_empty_api_key_raises_unavailable_without_constructing_client(monkeypatch):
    constructed = False

    def _record_construction(**kwargs):
        nonlocal constructed
        constructed = True
        return _FakeAnthropicClient(response=_FakeAnthropicResponse(content=[]))

    monkeypatch.setattr(anthropic_provider_module.anthropic, "AsyncAnthropic", _record_construction)

    provider = AnthropicProvider(_settings_anthropic(key=""))
    with pytest.raises(AnalysisUnavailable):
        await provider.generate(_sample_evidence())
    assert constructed is False


async def test_anthropic_empty_api_key_via_engine(monkeypatch):
    constructed = False

    def _record_construction(**kwargs):
        nonlocal constructed
        constructed = True
        return _FakeAnthropicClient(response=_FakeAnthropicResponse(content=[]))

    monkeypatch.setattr(anthropic_provider_module.anthropic, "AsyncAnthropic", _record_construction)

    settings = Settings(anthropic_api_key="", analysis_provider="anthropic")
    with pytest.raises(AnalysisUnavailable):
        await engine_module.analyze(_sample_evidence(), settings=settings)
    assert constructed is False


async def test_anthropic_web_search_disabled_omits_tool(monkeypatch):
    fake_client = _FakeAnthropicClient(
        response=_FakeAnthropicResponse(content=[_FakeTextBlock(_VALID_ANALYSIS_JSON)])
    )
    monkeypatch.setattr(anthropic_provider_module.anthropic, "AsyncAnthropic", lambda **kw: fake_client)

    provider = AnthropicProvider(_settings_anthropic(web_search=False))
    await provider.generate(_sample_evidence())

    assert "tools" not in fake_client.messages.last_kwargs


async def test_anthropic_web_search_enabled_includes_tool(monkeypatch):
    fake_client = _FakeAnthropicClient(
        response=_FakeAnthropicResponse(content=[_FakeTextBlock(_VALID_ANALYSIS_JSON)])
    )
    monkeypatch.setattr(anthropic_provider_module.anthropic, "AsyncAnthropic", lambda **kw: fake_client)

    provider = AnthropicProvider(_settings_anthropic(web_search=True))
    await provider.generate(_sample_evidence())

    assert "tools" in fake_client.messages.last_kwargs


def _fake_httpx_response(status_code):
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return httpx.Response(status_code=status_code, request=request)


# ============================================================
# Gemini provider: fakes
# ============================================================

class _FakeGeminiPromptFeedback:
    def __init__(self, block_reason=None):
        self.block_reason = block_reason


class _FakeGeminiWeb:
    def __init__(self, title=None, uri=None):
        self.title = title
        self.uri = uri


class _FakeGeminiChunk:
    def __init__(self, web=None):
        self.web = web


class _FakeGeminiGroundingMetadata:
    def __init__(self, grounding_chunks=None):
        self.grounding_chunks = grounding_chunks or []


class _FakeGeminiCandidate:
    def __init__(self, finish_reason=None, grounding_metadata=None):
        self.finish_reason = finish_reason
        self.grounding_metadata = grounding_metadata


class _FakeGeminiResponse:
    def __init__(self, text=None, candidates=None, prompt_feedback=None):
        self.text = text
        self.candidates = candidates if candidates is not None else [_FakeGeminiCandidate()]
        self.prompt_feedback = prompt_feedback


class _FakeGeminiModels:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.last_kwargs = None

    async def generate_content(self, **kwargs):
        self.last_kwargs = kwargs
        if self._exc is not None:
            raise self._exc
        return self._response


class _FakeGeminiAio:
    def __init__(self, models):
        self.models = models


class _FakeGeminiClient:
    def __init__(self, models, **kwargs):
        self.aio = _FakeGeminiAio(models)


def _fake_gemini_response(text=_VALID_ANALYSIS_JSON, **kwargs):
    return _FakeGeminiResponse(text=text, **kwargs)


def _patch_gemini_client(monkeypatch, response=None, exc=None):
    models = _FakeGeminiModels(response=response, exc=exc)
    monkeypatch.setattr(gemini_provider_module.genai, "Client", lambda **kw: _FakeGeminiClient(models))
    return models


def _settings_gemini(key="test-key", web_search=True):
    return Settings(gemini_api_key=key, analysis_provider="gemini", analysis_web_search=web_search)


# --- Gemini error mapping ---

async def test_gemini_rate_limited(monkeypatch):
    exc = genai_errors.ClientError(429, {"error": {"message": "rate limited"}})
    _patch_gemini_client(monkeypatch, exc=exc)

    provider = GeminiProvider(_settings_gemini())
    with pytest.raises(AnalysisRateLimited):
        await provider.generate(_sample_evidence())


async def test_gemini_auth_error_maps_to_unavailable(monkeypatch):
    exc = genai_errors.ClientError(403, {"error": {"message": "forbidden"}})
    _patch_gemini_client(monkeypatch, exc=exc)

    provider = GeminiProvider(_settings_gemini())
    with pytest.raises(AnalysisUnavailable):
        await provider.generate(_sample_evidence())


async def test_gemini_other_client_error_maps_to_provider_error(monkeypatch):
    exc = genai_errors.ClientError(400, {"error": {"message": "bad request"}})
    _patch_gemini_client(monkeypatch, exc=exc)

    provider = GeminiProvider(_settings_gemini())
    with pytest.raises(AnalysisProviderError):
        await provider.generate(_sample_evidence())


async def test_gemini_server_error_maps_to_provider_error(monkeypatch):
    exc = genai_errors.ServerError(500, {"error": {"message": "internal error"}})
    _patch_gemini_client(monkeypatch, exc=exc)

    provider = GeminiProvider(_settings_gemini())
    with pytest.raises(AnalysisProviderError):
        await provider.generate(_sample_evidence())


async def test_gemini_timeout(monkeypatch):
    _patch_gemini_client(monkeypatch, response=_fake_gemini_response())

    async def _raise_timeout(*args, **kwargs):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(gemini_provider_module.asyncio, "wait_for", _raise_timeout)

    provider = GeminiProvider(_settings_gemini())
    with pytest.raises(AnalysisTimeout):
        await provider.generate(_sample_evidence())


async def test_gemini_blocked_prompt_raises_refused(monkeypatch):
    response = _FakeGeminiResponse(
        text=None, prompt_feedback=_FakeGeminiPromptFeedback(block_reason="SAFETY")
    )
    _patch_gemini_client(monkeypatch, response=response)

    provider = GeminiProvider(_settings_gemini())
    with pytest.raises(AnalysisRefused):
        await provider.generate(_sample_evidence())


async def test_gemini_safety_finish_reason_raises_refused(monkeypatch):
    response = _FakeGeminiResponse(
        text=None, candidates=[_FakeGeminiCandidate(finish_reason="SAFETY")]
    )
    _patch_gemini_client(monkeypatch, response=response)

    provider = GeminiProvider(_settings_gemini())
    with pytest.raises(AnalysisRefused):
        await provider.generate(_sample_evidence())


async def test_gemini_empty_text_raises_refused(monkeypatch):
    response = _FakeGeminiResponse(text="")
    _patch_gemini_client(monkeypatch, response=response)

    provider = GeminiProvider(_settings_gemini())
    with pytest.raises(AnalysisRefused):
        await provider.generate(_sample_evidence())


async def test_gemini_empty_api_key_raises_unavailable_without_constructing_client(monkeypatch):
    constructed = False

    def _record_construction(**kwargs):
        nonlocal constructed
        constructed = True
        return _FakeGeminiClient(_FakeGeminiModels(response=_fake_gemini_response()))

    monkeypatch.setattr(gemini_provider_module.genai, "Client", _record_construction)

    provider = GeminiProvider(_settings_gemini(key=""))
    with pytest.raises(AnalysisUnavailable):
        await provider.generate(_sample_evidence())
    assert constructed is False


async def test_gemini_empty_api_key_via_engine(monkeypatch):
    constructed = False

    def _record_construction(**kwargs):
        nonlocal constructed
        constructed = True
        return _FakeGeminiClient(_FakeGeminiModels(response=_fake_gemini_response()))

    monkeypatch.setattr(gemini_provider_module.genai, "Client", _record_construction)

    settings = Settings(gemini_api_key="", analysis_provider="gemini")
    with pytest.raises(AnalysisUnavailable):
        await engine_module.analyze(_sample_evidence(), settings=settings)
    assert constructed is False


async def test_gemini_web_search_disabled_omits_grounding_tool_and_uses_response_schema(monkeypatch):
    models = _patch_gemini_client(monkeypatch, response=_fake_gemini_response())

    provider = GeminiProvider(_settings_gemini(web_search=False))
    await provider.generate(_sample_evidence())

    config = models.last_kwargs["config"]
    assert not getattr(config, "tools", None)
    assert config.response_schema is not None


async def test_gemini_web_search_enabled_includes_grounding_tool(monkeypatch):
    models = _patch_gemini_client(monkeypatch, response=_fake_gemini_response())

    provider = GeminiProvider(_settings_gemini(web_search=True))
    await provider.generate(_sample_evidence())

    config = models.last_kwargs["config"]
    assert config.tools


async def test_gemini_grounding_sources_populate_empty_analysis_sources(monkeypatch):
    empty_sources_json = json.dumps(
        {
            "headline": "Stock fell on sector-wide weakness",
            "confidence": "medium",
            "drivers": [],
            "sector_context": None,
            "what_to_watch": [],
            "sources": [],
        }
    )
    chunks = [
        _FakeGeminiChunk(web=_FakeGeminiWeb(title="Example News", uri="https://example.com/a")),
        _FakeGeminiChunk(web=None),  # non-web chunk, should be skipped
        _FakeGeminiChunk(web=_FakeGeminiWeb(title=None, uri="https://example.com/b")),
    ]
    response = _FakeGeminiResponse(
        text=empty_sources_json,
        candidates=[
            _FakeGeminiCandidate(
                finish_reason="STOP",
                grounding_metadata=_FakeGeminiGroundingMetadata(grounding_chunks=chunks),
            )
        ],
    )
    _patch_gemini_client(monkeypatch, response=response)

    provider = GeminiProvider(_settings_gemini(web_search=True))
    result = await provider.generate(_sample_evidence())

    assert len(result.sources) == 2
    assert result.sources[0].url == "https://example.com/a"
    assert result.sources[0].title == "Example News"
    assert result.sources[1].url == "https://example.com/b"
    assert result.sources[1].title == "https://example.com/b"


async def test_gemini_does_not_overwrite_existing_sources(monkeypatch):
    # The model-returned source URL must itself be legitimate (present in
    # supplied evidence) for this test to demonstrate "grounding doesn't
    # clobber an existing, valid sources list" rather than accidentally
    # demonstrating the (also correct) source-filtering behavior instead.
    evidence = _sample_evidence().model_copy(
        update={
            "news": [
                NewsItem(
                    title="Real headline",
                    publisher="Reuters",
                    url="https://example.com/real",
                    published_at=None,
                    summary=None,
                )
            ]
        }
    )
    chunks = [_FakeGeminiChunk(web=_FakeGeminiWeb(title="Should not appear", uri="https://example.com/z"))]
    with_sources_json = json.dumps(
        {
            "headline": "h",
            "confidence": "medium",
            "drivers": [],
            "sector_context": None,
            "what_to_watch": [],
            "sources": [{"title": "Real source", "url": "https://example.com/real"}],
        }
    )
    response = _FakeGeminiResponse(
        text=with_sources_json,
        candidates=[
            _FakeGeminiCandidate(
                finish_reason="STOP",
                grounding_metadata=_FakeGeminiGroundingMetadata(grounding_chunks=chunks),
            )
        ],
    )
    _patch_gemini_client(monkeypatch, response=response)

    provider = GeminiProvider(_settings_gemini(web_search=True))
    result = await provider.generate(evidence)

    assert len(result.sources) == 1
    assert result.sources[0].url == "https://example.com/real"


# ============================================================
# settings.analysis_model mutation after a successful Gemini run
# ============================================================

async def test_analysis_model_mutated_to_gemini_model_after_success(monkeypatch):
    _patch_gemini_client(monkeypatch, response=_fake_gemini_response())

    settings = Settings(gemini_api_key="key", analysis_provider="gemini")
    await engine_module.analyze(_sample_evidence(), settings=settings)

    assert settings.analysis_model == settings.gemini_model


# ============================================================
# config defaults: free-tier-safe out of the box
# ============================================================

def test_default_analysis_web_search_is_false():
    # Grounding is not free-tier eligible on Gemini (returns 429
    # RESOURCE_EXHAUSTED), so the out-of-the-box config must not enable it.
    assert Settings().analysis_web_search is False


def test_default_gemini_model_is_pinned_not_rolling_alias():
    # gemini-flash-latest is a rolling alias that can roll onto a model with
    # no free-tier quota (this happened) -- the default must be a dated,
    # verified-working model instead.
    assert Settings().gemini_model == "gemini-3.5-flash"


# ============================================================
# _to_gemini_schema: standard-JSON-Schema -> Gemini types.Schema dialect
# ============================================================

def test_to_gemini_schema_nullable_union_becomes_type_plus_nullable():
    converted = gemini_provider_module._to_gemini_schema({"type": ["string", "null"]})
    assert converted == {"type": "STRING", "nullable": True}


def test_to_gemini_schema_plain_type_is_uppercased_no_nullable_key():
    converted = gemini_provider_module._to_gemini_schema({"type": "string"})
    assert converted == {"type": "STRING"}
    assert "nullable" not in converted


def test_to_gemini_schema_recurses_into_object_properties():
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "note": {"type": ["string", "null"]},
        },
        "required": ["name"],
    }
    converted = gemini_provider_module._to_gemini_schema(schema)
    assert converted["type"] == "OBJECT"
    assert converted["properties"]["name"] == {"type": "STRING"}
    assert converted["properties"]["note"] == {"type": "STRING", "nullable": True}
    assert converted["required"] == ["name"]


def test_to_gemini_schema_recurses_into_array_items_object():
    # drivers/sources shape: array of objects
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
    }
    converted = gemini_provider_module._to_gemini_schema(schema)
    assert converted["type"] == "ARRAY"
    assert converted["items"]["type"] == "OBJECT"
    assert converted["items"]["properties"]["title"] == {"type": "STRING"}
    assert converted["items"]["required"] == ["title"]


def test_to_gemini_schema_preserves_enum():
    schema = {"type": "string", "enum": ["news", "sector", "technical", "fundamental"]}
    converted = gemini_provider_module._to_gemini_schema(schema)
    assert converted["enum"] == ["news", "sector", "technical", "fundamental"]


def test_to_gemini_schema_converts_full_shared_schema_without_error():
    converted = gemini_provider_module._to_gemini_schema(_TICKER_ANALYSIS_SCHEMA)
    assert converted["type"] == "OBJECT"
    assert converted["properties"]["sector_context"] == {"type": "STRING", "nullable": True}
    driver_items = converted["properties"]["drivers"]["items"]
    assert driver_items["type"] == "OBJECT"
    assert driver_items["properties"]["evidence_type"]["enum"] == [
        "news",
        "sector",
        "technical",
        "fundamental",
    ]
    assert converted["required"] == _TICKER_ANALYSIS_SCHEMA["required"]


def test_converted_gemini_schema_constructs_a_real_types_schema():
    # This is the check that would have actually caught the production bug:
    # the mocked unit tests all passed while the real SDK rejected the
    # standard-JSON-Schema nullable union at construction time
    # (pydantic_core.ValidationError on properties.sector_context.type).
    # Validating against the real types.Schema model here catches any
    # future drift the same way.
    from google.genai import types as genai_types

    validated = genai_types.Schema.model_validate(gemini_provider_module._GEMINI_TICKER_ANALYSIS_SCHEMA)
    assert validated.type == genai_types.Type.OBJECT


# ============================================================
# Anthropic provider: shared schema passed through unchanged
# ============================================================

def test_anthropic_structured_output_schema_keeps_standard_json_schema_nullable_union():
    # The Anthropic provider must NOT be touched by the Gemini-only
    # conversion -- it relies on the standard-JSON-Schema nullable union
    # form as-is.
    schema = _TICKER_ANALYSIS_SCHEMA
    assert schema["properties"]["sector_context"]["type"] == ["string", "null"]


async def test_anthropic_uses_unconverted_shared_schema_in_request(monkeypatch):
    fake_client = _FakeAnthropicClient(
        response=_FakeAnthropicResponse(content=[_FakeTextBlock(_VALID_ANALYSIS_JSON)])
    )
    monkeypatch.setattr(anthropic_provider_module.anthropic, "AsyncAnthropic", lambda **kw: fake_client)

    provider = AnthropicProvider(_settings_anthropic())
    await provider.generate(_sample_evidence())

    sent_schema = fake_client.messages.last_kwargs["output_config"]["format"]["schema"]
    assert sent_schema is _TICKER_ANALYSIS_SCHEMA


# ============================================================
# source-URL filtering + evidence_basis (providers/base.py: postprocess_analysis)
# ============================================================

def _evidence_with_news(urls):
    evidence = _sample_evidence()
    return evidence.model_copy(
        update={
            "news": [
                NewsItem(title=f"Headline {i}", publisher="Reuters", url=u, published_at=None, summary=None)
                for i, u in enumerate(urls)
            ]
        }
    )


def _analysis_with_sources(sources):
    from screener.analysis.models import AnalysisSource, TickerAnalysis

    return TickerAnalysis(
        headline="h",
        confidence="medium",
        drivers=[],
        sector_context=None,
        what_to_watch=[],
        sources=[AnalysisSource(title=t, url=u) for t, u in sources],
    )


def test_postprocess_drops_source_url_absent_from_evidence():
    evidence = _evidence_with_news(["https://example.com/real"])
    analysis = _analysis_with_sources([("Fabricated", "https://example.com/fabricated")])

    result = postprocess_analysis(
        analysis, evidence, allowed_source_urls={"https://example.com/real"}, web_search_used=False
    )

    assert result.sources == []


def test_postprocess_keeps_source_url_present_in_evidence():
    evidence = _evidence_with_news(["https://example.com/real"])
    analysis = _analysis_with_sources([("Real", "https://example.com/real")])

    result = postprocess_analysis(
        analysis, evidence, allowed_source_urls={"https://example.com/real"}, web_search_used=False
    )

    assert len(result.sources) == 1
    assert result.sources[0].url == "https://example.com/real"


def test_postprocess_url_normalization_trailing_slash_and_case_do_not_cause_drop():
    evidence = _evidence_with_news(["https://Example.com/real/"])
    analysis = _analysis_with_sources([("Real", "https://example.com/real")])

    result = postprocess_analysis(
        analysis,
        evidence,
        allowed_source_urls={"https://Example.com/real/"},
        web_search_used=False,
    )

    assert len(result.sources) == 1


def test_postprocess_all_invented_sources_yields_empty_list_not_error():
    evidence = _evidence_with_news([])
    analysis = _analysis_with_sources(
        [("Fake 1", "https://example.com/fake1"), ("Fake 2", "https://example.com/fake2")]
    )

    result = postprocess_analysis(analysis, evidence, allowed_source_urls=set(), web_search_used=False)

    assert result.sources == []


def test_postprocess_accepts_grounding_urls_when_grounding_on():
    evidence = _evidence_with_news([])
    analysis = _analysis_with_sources([("Grounded", "https://grounded.example.com/article")])

    result = postprocess_analysis(
        analysis,
        evidence,
        allowed_source_urls={"https://grounded.example.com/article"},
        web_search_used=True,
    )

    assert len(result.sources) == 1
    assert result.evidence_basis.web_search_used is True


def test_postprocess_evidence_basis_reflects_evidence_not_model_output():
    evidence = _evidence_with_news(["https://example.com/a", "https://example.com/b"])
    evidence = evidence.model_copy(
        update={"peers": [PeerMove(ticker="MSFT", change_pct=-1.0, sector="Technology")]}
    )
    analysis = _analysis_with_sources([])
    # A model trying to smuggle its own evidence_basis must not win.
    analysis = analysis.model_copy(
        update={"evidence_basis": {"news_count": 999, "peers_count": 999, "has_technicals": False, "web_search_used": True}}
    )

    result = postprocess_analysis(analysis, evidence, allowed_source_urls=set(), web_search_used=False)

    assert result.evidence_basis.news_count == 2
    assert result.evidence_basis.peers_count == 1
    assert result.evidence_basis.has_technicals is True
    assert result.evidence_basis.web_search_used is False


def test_normalize_url_strips_trailing_slash_and_lowercases_scheme_host():
    assert normalize_url("HTTPS://Example.com/Path/") == normalize_url("https://example.com/Path")


def test_normalize_url_empty_and_none_are_safe():
    assert normalize_url(None) == ""
    assert normalize_url("") == ""


async def test_gemini_fabricated_source_url_is_dropped(monkeypatch):
    fabricated_json = json.dumps(
        {
            "headline": "h",
            "confidence": "medium",
            "drivers": [],
            "sector_context": None,
            "what_to_watch": [],
            "sources": [{"title": "Invented", "url": "https://invented.example.com/x"}],
        }
    )
    response = _FakeGeminiResponse(text=fabricated_json)
    _patch_gemini_client(monkeypatch, response=response)

    provider = GeminiProvider(_settings_gemini(web_search=False))
    result = await provider.generate(_sample_evidence())

    assert result.sources == []


async def test_anthropic_fabricated_source_url_is_dropped(monkeypatch):
    fabricated_json = json.dumps(
        {
            "headline": "h",
            "confidence": "medium",
            "drivers": [],
            "sector_context": None,
            "what_to_watch": [],
            "sources": [{"title": "Invented", "url": "https://invented.example.com/x"}],
        }
    )
    fake_client = _FakeAnthropicClient(
        response=_FakeAnthropicResponse(content=[_FakeTextBlock(fabricated_json)])
    )
    monkeypatch.setattr(anthropic_provider_module.anthropic, "AsyncAnthropic", lambda **kw: fake_client)

    provider = AnthropicProvider(_settings_anthropic())
    result = await provider.generate(_sample_evidence())

    assert result.sources == []


async def test_anthropic_web_search_result_url_is_accepted(monkeypatch):
    real_json = json.dumps(
        {
            "headline": "h",
            "confidence": "medium",
            "drivers": [],
            "sector_context": None,
            "what_to_watch": [],
            "sources": [{"title": "Found via search", "url": "https://searched.example.com/x"}],
        }
    )

    class _FakeWebSearchResultItem:
        def __init__(self, url):
            self.url = url

    class _FakeWebSearchToolResultBlock:
        type = "web_search_tool_result"

        def __init__(self, urls):
            self.content = [_FakeWebSearchResultItem(u) for u in urls]

    fake_client = _FakeAnthropicClient(
        response=_FakeAnthropicResponse(
            content=[
                _FakeWebSearchToolResultBlock(["https://searched.example.com/x"]),
                _FakeTextBlock(real_json),
            ]
        )
    )
    monkeypatch.setattr(anthropic_provider_module.anthropic, "AsyncAnthropic", lambda **kw: fake_client)

    provider = AnthropicProvider(_settings_anthropic(web_search=True))
    result = await provider.generate(_sample_evidence())

    assert len(result.sources) == 1
    assert result.sources[0].url == "https://searched.example.com/x"


async def test_evidence_basis_populated_from_evidence_via_engine(monkeypatch):
    _patch_gemini_client(monkeypatch, response=_fake_gemini_response())

    settings = Settings(gemini_api_key="key", analysis_provider="gemini")
    evidence = _sample_evidence().model_copy(
        update={
            "news": [
                NewsItem(
                    title="H", publisher="P", url="https://example.com/n", published_at=None, summary=None
                )
            ]
        }
    )
    result = await engine_module.analyze(evidence, settings=settings)

    assert result.evidence_basis is not None
    assert result.evidence_basis.news_count == 1
    assert result.evidence_basis.has_technicals is True


# ============================================================
# system prompt: key grounding/anti-fabrication constraints present
# ============================================================

def test_system_prompt_contains_key_grounding_constraints():
    prompt = _SYSTEM_PROMPT
    assert "stale" in prompt.lower()
    assert "sources" in prompt.lower() and "only contain urls" in prompt.lower()
    assert "low" in prompt and "confidence" in prompt.lower()
    assert "no clear catalyst" in prompt.lower() or "no company-specific catalyst" in prompt.lower()
    assert "fabricat" in prompt.lower() or "invent" in prompt.lower()


def test_build_user_content_delimits_evidence_and_includes_dates():
    evidence = _sample_evidence()
    content = build_user_content(evidence)
    assert "BEGIN SUPPLIED EVIDENCE" in content
    assert "END SUPPLIED EVIDENCE" in content
    assert "Today's date" in content
    assert "as_of" in content.lower()
