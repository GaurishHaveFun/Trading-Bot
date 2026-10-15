"""Provider-agnostic shared pieces for the analysis engine: the
`AnalysisProvider` ABC plus the system prompt, JSON schema, and JSON
parsing helpers reused by every concrete provider (Anthropic, Gemini, ...)."""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from urllib.parse import urlsplit

from screener.analysis.context import evidence_to_prompt_payload
from screener.analysis.errors import AnalysisParseError
from screener.analysis.models import AnalysisEvidence, AnalysisSource, EvidenceBasis, TickerAnalysis
from screener.utils.logging import get_logger

logger = get_logger(__name__)


_SYSTEM_PROMPT = """\
You are analyzing why a specific stock has recently fallen in price. You are \
given a JSON evidence bundle for one ticker -- quote/profile data, computed \
technical indicators, same-sector peer moves, and recent news headlines -- \
and you may also use web search to find additional, verifiable context.

Your own training data is stale and must NOT be used as a source of facts \
about this ticker's recent price action. You do not know what has happened \
to this company since your training cutoff. The user message supplies \
today's date and the evidence's as_of timestamp precisely so that you rely \
on those and the supplied evidence instead of your own memory of "recent" \
events, which may be months or years out of date by now.

Every driver you report must be traceable to something concrete in the \
supplied evidence -- a specific headline, a technical reading (e.g. RSI, \
volume vs. average, distance from a 52-week extreme), a peer's move, or a \
fundamental figure -- or, when web search is available, a fact you found \
and cited via an actual web search call made this conversation. Each \
driver's `explanation` must make clear which piece of evidence it is \
grounded in (for example, "per the headline '...'" or "RSI_14 of X \
indicates..."), in 1-3 sentences of plain, non-jargon language. You must \
NOT assert any recent news, event, product launch, executive change, or \
deal involving this company unless it appears, in substance, in the \
supplied news headlines or in a web search result you actually retrieved \
this call. Do not recall such an event from memory and present it as \
current.

It is common, and expected, for the supplied evidence to contain no \
company-specific catalyst at all -- just routine headlines, unremarkable \
technicals, and a sector that is broadly down. When that happens, the \
correct answer is to say so plainly: attribute the move to sector or \
broad-market drift, or to technical factors, and set `confidence` to \
"low". A low-confidence "no clear catalyst found" response is a SUCCESS, \
not a failure -- do not manufacture three tidy drivers because a thin or \
empty evidence bundle feels like an inadequate answer. Fabricating a \
plausible-sounding catalyst is always worse than admitting you don't have \
one.

The `sources` field may ONLY contain URLs that are present in the supplied \
evidence's news items, or that were returned to you by an actual web \
search call during this conversation. Inventing a URL, or recalling one \
from training data, is a failure condition -- even if the URL "looks \
right" or you are confident it exists. If you have no such URL for a \
claim, omit the source rather than guessing one. An empty `sources` list \
is a perfectly fine, and common, result.

You must never give buy, sell, or hold recommendations, and you must never \
predict future prices or returns. Stay strictly descriptive and explanatory.

Respond with ONLY a single JSON object matching the required schema. Do not \
include any prose, markdown fences, or commentary outside that JSON object \
(this is also mechanically enforced by structured output configuration when \
enabled, but you must not rely on that alone)."""


_DRIVER_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "explanation": {"type": "string"},
        "evidence_type": {
            "type": "string",
            "enum": ["news", "sector", "technical", "fundamental"],
        },
    },
    "required": ["title", "explanation", "evidence_type"],
    "additionalProperties": False,
}

_SOURCE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "url": {"type": "string"},
    },
    "required": ["title", "url"],
    "additionalProperties": False,
}

_TICKER_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "drivers": {"type": "array", "items": _DRIVER_SCHEMA},
        "sector_context": {"type": ["string", "null"]},
        "what_to_watch": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": _SOURCE_SCHEMA},
    },
    "required": [
        "headline",
        "confidence",
        "drivers",
        "sector_context",
        "what_to_watch",
        "sources",
    ],
    "additionalProperties": False,
}


def strip_json_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def build_user_content(evidence: AnalysisEvidence) -> str:
    payload = evidence_to_prompt_payload(evidence)
    today = datetime.now(timezone.utc).date().isoformat()
    return (
        f"Today's date (UTC): {today}\n"
        f"Evidence as_of (UTC): {evidence.as_of.isoformat()}\n\n"
        "=== BEGIN SUPPLIED EVIDENCE ===\n"
        "The JSON object below is the ONLY ground truth you have for this "
        f"ticker ({evidence.ticker}), besides anything you retrieve via an "
        "actual web search call this conversation. Do not blend in facts "
        "from your own training data.\n\n"
        f"{json.dumps(payload, indent=2)}\n"
        "=== END SUPPLIED EVIDENCE ===\n\n"
        "Explain the price decline using ONLY this evidence and, if "
        "available, web search. If the evidence contains no company-"
        "specific catalyst, say so and set confidence to \"low\" rather "
        "than inventing one."
    )


def normalize_url(url: str | None) -> str:
    """Normalize a URL for set-membership comparison: strip surrounding
    whitespace, lowercase the scheme/host, and drop a trailing slash from
    the path, so a harmless formatting difference (trailing slash, URL
    case) between a model-echoed URL and the one it was actually supplied
    doesn't cause a legitimate source to be dropped as fabricated."""
    if not url:
        return ""
    url = url.strip()
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url.rstrip("/").lower()
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/")
    normalized = f"{scheme}://{netloc}{path}"
    if parts.query:
        normalized = f"{normalized}?{parts.query}"
    return normalized


def postprocess_analysis(
    analysis: TickerAnalysis,
    evidence: AnalysisEvidence,
    *,
    allowed_source_urls: set[str],
    web_search_used: bool,
) -> TickerAnalysis:
    """Shared post-processing every provider must run on its parsed
    response, since a prompt instruction is only a request and not a
    guarantee:

    1. Drop any `sources` entry whose URL is not in `allowed_source_urls`
       (the URLs the model was actually, legitimately given -- every
       `NewsItem.url` in the evidence, plus, when grounding/web-search was
       used, the URLs the provider's grounding metadata / search results
       returned). Comparison is done on normalized URLs so a formatting
       difference doesn't discard a legitimate source. An empty result is
       valid and is not an error. Dropped entries are logged, never
       silently discarded, since silent dropping would hide the exact
       failure mode this exists to catch.
    2. Overwrite `evidence_basis` with a machine-computed summary of what
       the evidence actually contained -- never trust a value the model's
       own JSON output tried to set for this field.
    """
    normalized_allowed = {normalize_url(u) for u in allowed_source_urls if u}

    kept: list[AnalysisSource] = []
    dropped = 0
    for source in analysis.sources:
        if normalize_url(source.url) in normalized_allowed:
            kept.append(source)
        else:
            dropped += 1

    if dropped:
        logger.warning(
            "analysis_sources_filtered",
            ticker=evidence.ticker,
            dropped_count=dropped,
        )

    evidence_basis = EvidenceBasis(
        news_count=len(evidence.news),
        peers_count=len(evidence.peers),
        has_technicals=bool(evidence.technicals),
        web_search_used=web_search_used,
    )

    return analysis.model_copy(update={"sources": kept, "evidence_basis": evidence_basis})


def parse_analysis_json(text: str, *, ticker: str) -> TickerAnalysis:
    """Strip markdown fences (if any), parse JSON, and validate into a
    `TickerAnalysis`. Raises `AnalysisParseError` on any failure, logging a
    warning first."""
    try:
        cleaned = strip_json_fences(text)
        parsed = json.loads(cleaned)
        return TickerAnalysis.model_validate(parsed)
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        logger.warning("analysis_parse_error", ticker=ticker, error=str(exc))
        raise AnalysisParseError(str(exc)) from exc


class AnalysisProvider(ABC):
    """Common interface every concrete analysis provider implements."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for this provider, e.g. 'anthropic' / 'gemini'."""

    @property
    @abstractmethod
    def model(self) -> str:
        """The exact model ID string this provider instance will call."""

    @abstractmethod
    async def generate(self, evidence: AnalysisEvidence) -> TickerAnalysis:
        """Ask the underlying LLM to explain `evidence`'s ticker's price
        decline and return the validated `TickerAnalysis`."""
