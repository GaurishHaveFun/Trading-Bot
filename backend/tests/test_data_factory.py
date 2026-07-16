"""Tests for the provider factory + fallback wiring (Milestone 4)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from screener.config import Settings
from screener.data.factory import (
    _FallbackLosersUniverse,
    build_bar_provider,
    build_fundamentals_provider,
    build_universe_provider,
)
from screener.data.fundamentals_provider import FundamentalsProvider
from screener.data.schwab.auth import SchwabAuthExpired
from screener.data.yfinance_provider import YFinanceProvider
from screener.models import Bar


def _settings(tmp_path, **overrides) -> Settings:
    defaults = dict(
        _env_file=None,
        data_provider="yfinance",
        universe="losers",
        schwab_app_key="x",
        schwab_app_secret="x",
        schwab_callback_url="https://127.0.0.1:8182",
        schwab_token_path=str(tmp_path / "token.json"),
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _bar(close: float = 100.0) -> Bar:
    return Bar(
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        open=close - 1,
        high=close + 1,
        low=close - 2,
        close=close,
        volume=1_000_000,
    )


# ---------------------------------------------------------------------------
# build_bar_provider
# ---------------------------------------------------------------------------


def test_build_bar_provider_returns_plain_yfinance_when_not_schwab(tmp_path):
    settings = _settings(tmp_path, data_provider="yfinance")
    cache = MagicMock()

    with patch("screener.data.factory.SchwabAuth") as mock_auth, \
         patch("screener.data.factory.SchwabClient") as mock_client:
        provider = build_bar_provider(settings, cache)

    assert type(provider) is YFinanceProvider
    mock_auth.assert_not_called()
    mock_client.assert_not_called()


async def test_build_bar_provider_falls_back_to_yfinance_on_schwab_failure(tmp_path):
    settings = _settings(tmp_path, data_provider="schwab")
    cache = MagicMock()

    fallback_bar = _bar(close=123.0)

    mock_schwab_provider = MagicMock()
    mock_schwab_provider.get_bars = AsyncMock(side_effect=SchwabAuthExpired("expired"))

    mock_yfinance_provider = MagicMock()
    mock_yfinance_provider.get_bars = AsyncMock(return_value=[fallback_bar])

    with patch("screener.data.factory.SchwabAuth"), \
         patch("screener.data.factory.SchwabClient"), \
         patch("screener.data.factory.SchwabProvider", return_value=mock_schwab_provider), \
         patch("screener.data.factory.YFinanceProvider", return_value=mock_yfinance_provider), \
         patch("screener.data.factory.logger.warning") as mock_warning:
        provider = build_bar_provider(settings, cache)
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 10, tzinfo=timezone.utc)
        result = await provider.get_bars("AAPL", start, end)

    assert result == [fallback_bar]
    mock_warning.assert_called_once()
    assert mock_warning.call_args[0][0] == "provider_fallback"


# ---------------------------------------------------------------------------
# build_universe_provider
# ---------------------------------------------------------------------------


async def test_build_universe_provider_schwab_success_never_touches_yfinance(tmp_path):
    settings = _settings(tmp_path, universe="losers", data_provider="schwab")
    watchlist = {"AAPL"}

    mock_schwab_universe = MagicMock()
    mock_schwab_universe.get_symbols = AsyncMock(return_value=["AAPL", "MSFT"])
    mock_schwab_universe.get_quotes = AsyncMock(return_value={"AAPL": {"price_to_book": 1.0}})

    with patch("screener.data.factory.SchwabAuth"), \
         patch("screener.data.factory.SchwabClient"), \
         patch("screener.data.factory.SchwabLosersUniverse", return_value=mock_schwab_universe), \
         patch("screener.data.factory.LosersUniverse") as mock_yfinance_cls:
        provider = build_universe_provider(settings, watchlist)
        symbols = await provider.get_symbols()
        quotes = await provider.get_quotes()

    assert symbols == ["AAPL", "MSFT"]
    assert quotes == {"AAPL": {"price_to_book": 1.0}}
    mock_yfinance_cls.assert_called_once()
    mock_yfinance_cls.return_value.get_symbols.assert_not_called()
    mock_yfinance_cls.return_value.get_quotes.assert_not_called()


async def test_build_universe_provider_falls_back_to_yfinance_on_schwab_failure(tmp_path):
    settings = _settings(tmp_path, universe="losers", data_provider="schwab")
    watchlist = {"AAPL"}

    mock_schwab_universe = MagicMock()
    mock_schwab_universe.get_symbols = AsyncMock(side_effect=RuntimeError("schwab down"))

    mock_yfinance_universe = MagicMock()
    mock_yfinance_universe.get_symbols = AsyncMock(return_value=["NFLX", "TSLA"])

    with patch("screener.data.factory.SchwabAuth"), \
         patch("screener.data.factory.SchwabClient"), \
         patch("screener.data.factory.SchwabLosersUniverse", return_value=mock_schwab_universe), \
         patch("screener.data.factory.LosersUniverse", return_value=mock_yfinance_universe), \
         patch("screener.data.factory.logger.warning") as mock_warning:
        provider = build_universe_provider(settings, watchlist)
        symbols = await provider.get_symbols()

    assert symbols == ["NFLX", "TSLA"]
    mock_warning.assert_called_once()
    assert mock_warning.call_args[0][0] == "provider_fallback"


async def test_fallback_losers_universe_get_quotes_routes_to_active_yfinance_provider():
    """State-consistency: after get_symbols() falls back to yfinance,
    get_quotes() must route to the yfinance provider's quotes, not stale/
    empty Schwab state."""
    mock_schwab_universe = MagicMock()
    mock_schwab_universe.get_symbols = AsyncMock(side_effect=RuntimeError("schwab down"))
    mock_schwab_universe.get_quotes = AsyncMock(return_value={"SHOULD_NOT_BE_USED": {}})

    mock_yfinance_universe = MagicMock()
    mock_yfinance_universe.get_symbols = AsyncMock(return_value=["NFLX"])
    mock_yfinance_universe.get_quotes = AsyncMock(return_value={"NFLX": {"price_to_book": 2.5}})

    fallback = _FallbackLosersUniverse(mock_schwab_universe, mock_yfinance_universe)

    symbols = await fallback.get_symbols()
    quotes = await fallback.get_quotes()

    assert symbols == ["NFLX"]
    assert quotes == {"NFLX": {"price_to_book": 2.5}}
    mock_schwab_universe.get_quotes.assert_not_called()


async def test_fallback_losers_universe_get_symbols_falls_back_on_empty_schwab_result():
    """Schwab's own error handling swallows HTTP/auth failures and returns an
    empty list instead of raising, so the wrapper must also treat an empty
    (falsy) result as a fallback trigger, not just an exception."""
    mock_schwab_universe = MagicMock()
    mock_schwab_universe.get_symbols = AsyncMock(return_value=[])

    mock_yfinance_universe = MagicMock()
    mock_yfinance_universe.get_symbols = AsyncMock(return_value=["NFLX"])
    mock_yfinance_universe.get_quotes = AsyncMock(return_value={"NFLX": {"price_to_book": 2.5}})

    fallback = _FallbackLosersUniverse(mock_schwab_universe, mock_yfinance_universe)

    symbols = await fallback.get_symbols()
    quotes = await fallback.get_quotes()

    assert symbols == ["NFLX"]
    assert quotes == {"NFLX": {"price_to_book": 2.5}}


async def test_fallback_losers_universe_get_quotes_falls_back_on_empty_schwab_result():
    """Same empty-result fallback behavior for get_quotes(): if Schwab is
    the active provider but returns an empty dict, fall back to yfinance
    and update _active accordingly."""
    mock_schwab_universe = MagicMock()
    mock_schwab_universe.get_quotes = AsyncMock(return_value={})

    mock_yfinance_universe = MagicMock()
    mock_yfinance_universe.get_quotes = AsyncMock(return_value={"NFLX": {"price_to_book": 2.5}})

    fallback = _FallbackLosersUniverse(mock_schwab_universe, mock_yfinance_universe)
    fallback._active = "schwab"

    quotes = await fallback.get_quotes()

    assert quotes == {"NFLX": {"price_to_book": 2.5}}
    assert fallback._active == "yfinance"


async def test_fallback_losers_universe_quote_for_falls_back_on_none_schwab_result():
    """quote_for() must fall back to yfinance when Schwab returns None
    (no exception), matching the same empty/falsy-result contract as
    get_symbols()/get_quotes()."""
    mock_schwab_universe = MagicMock()
    mock_schwab_universe.quote_for = AsyncMock(return_value=None)

    mock_yfinance_universe = MagicMock()
    mock_yfinance_universe.quote_for = AsyncMock(return_value={"price_to_book": 2.5})

    fallback = _FallbackLosersUniverse(mock_schwab_universe, mock_yfinance_universe)

    quote = await fallback.quote_for("NFLX")

    assert quote == {"price_to_book": 2.5}


# ---------------------------------------------------------------------------
# build_fundamentals_provider
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("data_provider", ["schwab", "yfinance"])
def test_build_fundamentals_provider_always_yfinance_backed(tmp_path, data_provider):
    settings = _settings(tmp_path, data_provider=data_provider)
    cache = MagicMock()

    with patch(
        "screener.data.schwab.fundamentals_provider.SchwabFundamentalsProvider"
    ) as mock_schwab_fundamentals:
        provider = build_fundamentals_provider(settings, cache)

    assert type(provider) is FundamentalsProvider
    mock_schwab_fundamentals.assert_not_called()


def test_factory_module_never_imports_schwab_fundamentals_provider():
    import screener.data.factory as factory_module

    assert not hasattr(factory_module, "SchwabFundamentalsProvider")
