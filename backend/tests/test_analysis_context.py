"""Unit tests for `screener.analysis.context`. No network access -- every
external dependency (`fetch_quote_info`, the bar provider, `LosersUniverse`,
`load_watchlist`, `fetch_news`) is monkeypatched with fakes."""
from datetime import datetime, timedelta, timezone

import pytest

import screener.analysis.context as context_module
from screener.analysis.models import AnalysisEvidence, NewsItem, PeerMove
from screener.config import Settings


class _FakeSettings(Settings):
    pass


class _FakeBar:
    def __init__(self, close, volume=1_000_000, open_=None, high=None, low=None):
        self.open = open_ if open_ is not None else close
        self.high = high if high is not None else close
        self.low = low if low is not None else close
        self.close = close
        self.volume = volume


class _FakeBarProvider:
    def __init__(self, bars=None, raise_error=False):
        self._bars = bars or []
        self._raise_error = raise_error

    async def get_bars(self, symbol, start, end):
        if self._raise_error:
            raise RuntimeError("bars provider exploded")
        return self._bars


class _FakeBarCache:
    def __init__(self, *args, **kwargs):
        pass


class _FakeLosersUniverse:
    def __init__(self, watchlist=None):
        self._watchlist = watchlist or set()

    async def get_symbols(self):
        return list(self._quotes().keys())

    async def get_quotes(self):
        return self._quotes()

    def _quotes(self):
        return {
            "PEERCO": {"sector": "Technology", "change_pct": -3.0},
            "OTHERCO": {"sector": "Healthcare", "change_pct": -8.0},
        }


def _real_info(**overrides):
    info = {
        "longName": "Test Company",
        "shortName": "TEST",
        "sector": "Technology",
        "industry": "Software",
        "regularMarketPrice": 100.0,
        "regularMarketChangePercent": -5.0,
        "priceToBook": 3.0,
        "currency": "USD",
    }
    info.update(overrides)
    return info


def _patch_common(monkeypatch, info, bar_provider=None, losers_cls=_FakeLosersUniverse, news_items=None):
    monkeypatch.setattr(context_module, "fetch_quote_info", lambda symbol: info)
    monkeypatch.setattr(context_module, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(context_module, "BarCache", _FakeBarCache)
    monkeypatch.setattr(context_module, "build_bar_provider", lambda settings, cache: bar_provider or _FakeBarProvider())
    monkeypatch.setattr(context_module, "LosersUniverse", losers_cls)
    monkeypatch.setattr(context_module, "load_watchlist", lambda path: {"AAPL"})

    async def _fake_fetch_news(symbol, *args, **kwargs):
        if isinstance(news_items, Exception):
            raise news_items
        return news_items or []

    monkeypatch.setattr(context_module, "fetch_news", _fake_fetch_news)

    # Reset the lazy singleton so each test builds a fresh fake provider.
    context_module._bar_cache = None
    context_module._bar_provider = None


# --- unknown symbol ---

async def test_build_evidence_returns_none_for_unknown_symbol(monkeypatch):
    monkeypatch.setattr(context_module, "fetch_quote_info", lambda symbol: {})
    monkeypatch.setattr(context_module, "get_settings", lambda: _FakeSettings())

    result = await context_module.build_evidence("ZZZZZZ")

    assert result is None


async def test_build_evidence_returns_none_when_info_is_none(monkeypatch):
    monkeypatch.setattr(context_module, "fetch_quote_info", lambda symbol: None)
    monkeypatch.setattr(context_module, "get_settings", lambda: _FakeSettings())

    result = await context_module.build_evidence("ZZZZZZ")

    assert result is None


# --- partial-failure resilience ---

async def test_build_evidence_survives_bar_provider_failure(monkeypatch):
    info = _real_info()
    _patch_common(monkeypatch, info, bar_provider=_FakeBarProvider(raise_error=True))

    result = await context_module.build_evidence("TEST")

    assert isinstance(result, AnalysisEvidence)
    assert result.technicals == {}
    assert result.ticker == "TEST"


async def test_build_evidence_survives_news_failure(monkeypatch):
    info = _real_info()
    _patch_common(monkeypatch, info, news_items=RuntimeError("news exploded"))

    result = await context_module.build_evidence("TEST")

    assert isinstance(result, AnalysisEvidence)
    assert result.news == []


async def test_build_evidence_populates_full_bundle(monkeypatch):
    info = _real_info()
    bars = [_FakeBar(close=100.0 + i) for i in range(30)]
    news = [
        NewsItem(
            title="Some news",
            publisher="Pub",
            url="https://example.com",
            published_at=datetime.now(timezone.utc) - timedelta(hours=1),
            summary="summary",
        )
    ]
    _patch_common(monkeypatch, info, bar_provider=_FakeBarProvider(bars=bars), news_items=news)

    result = await context_module.build_evidence("TEST")

    assert isinstance(result, AnalysisEvidence)
    assert result.profile["sector"] == "Technology"
    assert result.peers  # PEERCO shares the "Technology" sector
    assert all(p.sector == "Technology" for p in result.peers)
    assert result.news[0].title == "Some news"


# --- no NaN in technicals ---

async def test_technicals_never_contains_nan_for_short_history(monkeypatch):
    info = _real_info()
    # Only 5 bars: not enough for rsi(14) (needs >= 15).
    bars = [_FakeBar(close=100.0 + i) for i in range(5)]
    _patch_common(monkeypatch, info, bar_provider=_FakeBarProvider(bars=bars))

    result = await context_module.build_evidence("TEST")

    assert "rsi_14" not in result.technicals
    for value in result.technicals.values():
        if isinstance(value, float):
            assert value == value  # NaN != NaN


# --- evidence_to_prompt_payload is pure and deterministic ---

def test_evidence_to_prompt_payload_is_deterministic():
    evidence = AnalysisEvidence(
        ticker="TEST",
        as_of=datetime.now(timezone.utc),
        profile={"b": 1, "a": 2},
        stats={"z": 1, "y": 2},
        targets={"mean": 1.0},
        technicals={"rsi_14": 50.0, "change_pct_1d": -1.0},
        peers=[PeerMove(ticker="PEER", change_pct=-2.0, sector="Technology")],
        news=[
            NewsItem(
                title="Title",
                publisher="Pub",
                url="https://example.com",
                published_at=datetime.now(timezone.utc),
                summary="s",
            )
        ],
    )

    result1 = context_module.evidence_to_prompt_payload(evidence)
    result2 = context_module.evidence_to_prompt_payload(evidence)

    assert result1 == result2
    assert list(result1.keys()) == list(result2.keys())
    assert list(result1.keys()) == [
        "ticker",
        "as_of",
        "profile",
        "stats",
        "targets",
        "technicals",
        "peers",
        "news",
    ]
    # sorted() sub-dicts should also be stable.
    assert list(result1["profile"].keys()) == ["a", "b"]
