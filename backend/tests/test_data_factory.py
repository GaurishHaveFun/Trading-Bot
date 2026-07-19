"""Tests for the provider factory + fallback wiring (Milestone 4)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from screener.config import Settings
from screener.data.factory import (
    _FallbackLosersUniverse,
    _MergedFundamentalsProvider,
    build_bar_provider,
    build_fundamentals_provider,
    build_universe_provider,
)
from screener.data.fundamentals_provider import FundamentalsProvider
from screener.data.schwab.auth import SchwabAuthExpired
from screener.data.yfinance_provider import YFinanceProvider
from screener.models import Bar, FundamentalsSnapshot


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
    # industry/sector already present (truthy) so the industry/sector
    # enrichment helper short-circuits without touching yfinance/network.
    mock_schwab_universe.get_quotes = AsyncMock(
        return_value={
            "AAPL": {"price_to_book": 1.0, "industry": "Consumer Electronics", "sector": "Technology"}
        }
    )

    with patch("screener.data.factory.SchwabAuth"), \
         patch("screener.data.factory.SchwabClient"), \
         patch("screener.data.factory.SchwabLosersUniverse", return_value=mock_schwab_universe), \
         patch("screener.data.factory.LosersUniverse") as mock_yfinance_cls, \
         patch("screener.data.factory.yf.Ticker") as mock_ticker_cls:
        provider = build_universe_provider(settings, watchlist)
        symbols = await provider.get_symbols()
        quotes = await provider.get_quotes()

    assert symbols == ["AAPL", "MSFT"]
    assert quotes == {
        "AAPL": {"price_to_book": 1.0, "industry": "Consumer Electronics", "sector": "Technology"}
    }
    mock_yfinance_cls.assert_called_once()
    mock_ticker_cls.assert_not_called()
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
#
# NOTE: `test_factory_module_never_imports_schwab_fundamentals_provider` has
# been REMOVED (deliberate reversal). It used to assert factory.py does NOT
# import `SchwabFundamentalsProvider` at module level — that contract has
# been explicitly reversed now that the real Schwab schema confirms 3 real
# TTM metrics, and `build_fundamentals_provider` needs to construct a
# `SchwabFundamentalsProvider` (imported at module level, matching the
# existing pattern for `SchwabProvider`/`SchwabLosersUniverse`, so it can be
# patched via `screener.data.factory.SchwabFundamentalsProvider` in tests
# below).


def test_build_fundamentals_provider_plain_yfinance_when_not_schwab(tmp_path):
    settings = _settings(tmp_path, data_provider="yfinance")
    cache = MagicMock()

    with patch("screener.data.factory.SchwabAuth") as mock_auth, \
         patch("screener.data.factory.SchwabClient") as mock_client, \
         patch("screener.data.factory.SchwabFundamentalsProvider") as mock_schwab_fundamentals:
        provider = build_fundamentals_provider(settings, cache)

    assert type(provider) is FundamentalsProvider
    mock_auth.assert_not_called()
    mock_client.assert_not_called()
    mock_schwab_fundamentals.assert_not_called()


def test_build_fundamentals_provider_returns_merged_wrapper_when_schwab(tmp_path):
    settings = _settings(tmp_path, data_provider="schwab")
    cache = MagicMock()

    with patch("screener.data.factory.SchwabAuth"), \
         patch("screener.data.factory.SchwabClient"), \
         patch("screener.data.factory.SchwabFundamentalsProvider") as mock_schwab_fundamentals:
        provider = build_fundamentals_provider(settings, cache)

    assert type(provider) is _MergedFundamentalsProvider
    mock_schwab_fundamentals.assert_called_once()


# ---------------------------------------------------------------------------
# _MergedFundamentalsProvider
# ---------------------------------------------------------------------------


def _snapshot(**overrides) -> FundamentalsSnapshot:
    defaults = dict(
        ticker="AAPL",
        as_of=datetime(2024, 1, 1, tzinfo=timezone.utc),
        years_available=5,
        fcf_5y_cumulative=1000.0,
        interest_coverage=10.0,
        gross_margin=0.4,
        ocf_ni_ratio=1.1,
        net_margin=0.2,
        share_dilution_5y=0.05,
    )
    defaults.update(overrides)
    return FundamentalsSnapshot(**defaults)


async def test_merged_fundamentals_all_three_schwab_fields_populated():
    yfinance_snapshot = _snapshot()
    schwab_snapshot = _snapshot(
        interest_coverage=99.0,
        gross_margin=0.55,
        net_margin=0.33,
        years_available=0,
        fcf_5y_cumulative=None,
        ocf_ni_ratio=None,
        share_dilution_5y=None,
    )

    mock_yfinance = MagicMock()
    mock_yfinance.get_fundamentals = AsyncMock(return_value=yfinance_snapshot)
    mock_schwab = MagicMock()
    mock_schwab.get_fundamentals = AsyncMock(return_value=schwab_snapshot)

    merged = _MergedFundamentalsProvider(mock_schwab, mock_yfinance)
    result = await merged.get_fundamentals("AAPL")

    assert result.interest_coverage == 99.0
    assert result.gross_margin == 0.55
    assert result.net_margin == 0.33
    # yfinance remains the source of truth for these:
    assert result.years_available == yfinance_snapshot.years_available
    assert result.fcf_5y_cumulative == yfinance_snapshot.fcf_5y_cumulative
    assert result.ocf_ni_ratio == yfinance_snapshot.ocf_ni_ratio
    assert result.share_dilution_5y == yfinance_snapshot.share_dilution_5y
    assert result.ticker == yfinance_snapshot.ticker
    assert result.as_of == yfinance_snapshot.as_of


async def test_merged_fundamentals_one_schwab_field_none_falls_back_per_field():
    yfinance_snapshot = _snapshot(interest_coverage=10.0, gross_margin=0.4, net_margin=0.2)
    schwab_snapshot = _snapshot(
        interest_coverage=99.0,
        gross_margin=None,  # Schwab didn't have this one
        net_margin=0.33,
        years_available=0,
        fcf_5y_cumulative=None,
        ocf_ni_ratio=None,
        share_dilution_5y=None,
    )

    mock_yfinance = MagicMock()
    mock_yfinance.get_fundamentals = AsyncMock(return_value=yfinance_snapshot)
    mock_schwab = MagicMock()
    mock_schwab.get_fundamentals = AsyncMock(return_value=schwab_snapshot)

    merged = _MergedFundamentalsProvider(mock_schwab, mock_yfinance)
    result = await merged.get_fundamentals("AAPL")

    assert result.interest_coverage == 99.0
    assert result.gross_margin == 0.4  # fell back to yfinance
    assert result.net_margin == 0.33


async def test_merged_fundamentals_schwab_raises_returns_plain_yfinance_snapshot():
    yfinance_snapshot = _snapshot()

    mock_yfinance = MagicMock()
    mock_yfinance.get_fundamentals = AsyncMock(return_value=yfinance_snapshot)
    mock_schwab = MagicMock()
    mock_schwab.get_fundamentals = AsyncMock(side_effect=RuntimeError("schwab down"))

    merged = _MergedFundamentalsProvider(mock_schwab, mock_yfinance)
    result = await merged.get_fundamentals("AAPL")

    assert result == yfinance_snapshot


async def test_merged_fundamentals_base_none_returns_none_without_calling_schwab():
    mock_yfinance = MagicMock()
    mock_yfinance.get_fundamentals = AsyncMock(return_value=None)
    mock_schwab = MagicMock()
    mock_schwab.get_fundamentals = AsyncMock(return_value=_snapshot())

    merged = _MergedFundamentalsProvider(mock_schwab, mock_yfinance)
    result = await merged.get_fundamentals("AAPL")

    assert result is None
    mock_schwab.get_fundamentals.assert_not_called()


# ---------------------------------------------------------------------------
# _FallbackLosersUniverse industry/sector enrichment
# ---------------------------------------------------------------------------


async def test_get_quotes_enriches_schwab_sourced_quotes_missing_industry_sector():
    mock_schwab_universe = MagicMock()
    mock_schwab_universe.get_quotes = AsyncMock(
        return_value={"NVDA": {"price_to_book": 12.0, "industry": None, "sector": None}}
    )
    mock_yfinance_universe = MagicMock()

    fallback = _FallbackLosersUniverse(mock_schwab_universe, mock_yfinance_universe)
    fallback._active = "schwab"

    mock_info = {"industry": "Semiconductors", "sector": "Technology"}
    with patch("screener.data.factory.yf.Ticker") as mock_ticker_cls:
        mock_ticker_cls.return_value.info = mock_info
        quotes = await fallback.get_quotes()

    assert quotes["NVDA"]["industry"] == "Semiconductors"
    assert quotes["NVDA"]["sector"] == "Technology"
    mock_ticker_cls.assert_called_once_with("NVDA")


async def test_get_quotes_does_not_reenrich_yfinance_sourced_quotes():
    mock_schwab_universe = MagicMock()
    mock_yfinance_universe = MagicMock()
    mock_yfinance_universe.get_quotes = AsyncMock(
        return_value={"NFLX": {"price_to_book": 2.5, "industry": "Entertainment", "sector": "Comm"}}
    )

    fallback = _FallbackLosersUniverse(mock_schwab_universe, mock_yfinance_universe)
    fallback._active = "yfinance"

    with patch("screener.data.factory.yf.Ticker") as mock_ticker_cls:
        quotes = await fallback.get_quotes()

    assert quotes == {"NFLX": {"price_to_book": 2.5, "industry": "Entertainment", "sector": "Comm"}}
    mock_ticker_cls.assert_not_called()


async def test_quote_for_enriches_schwab_sourced_quote():
    mock_schwab_universe = MagicMock()
    mock_schwab_universe.quote_for = AsyncMock(
        return_value={"price_to_book": 12.0, "industry": None, "sector": None}
    )
    mock_yfinance_universe = MagicMock()

    fallback = _FallbackLosersUniverse(mock_schwab_universe, mock_yfinance_universe)

    mock_info = {"industry": "Semiconductors", "sector": "Technology"}
    with patch("screener.data.factory.yf.Ticker") as mock_ticker_cls:
        mock_ticker_cls.return_value.info = mock_info
        quote = await fallback.quote_for("NVDA")

    assert quote["industry"] == "Semiconductors"
    assert quote["sector"] == "Technology"


async def test_quote_for_does_not_reenrich_yfinance_fallback_quote():
    mock_schwab_universe = MagicMock()
    mock_schwab_universe.quote_for = AsyncMock(return_value=None)
    mock_yfinance_universe = MagicMock()
    mock_yfinance_universe.quote_for = AsyncMock(
        return_value={"price_to_book": 2.5, "industry": "Entertainment", "sector": "Comm"}
    )

    fallback = _FallbackLosersUniverse(mock_schwab_universe, mock_yfinance_universe)

    with patch("screener.data.factory.yf.Ticker") as mock_ticker_cls:
        quote = await fallback.quote_for("NFLX")

    assert quote == {"price_to_book": 2.5, "industry": "Entertainment", "sector": "Comm"}
    mock_ticker_cls.assert_not_called()
