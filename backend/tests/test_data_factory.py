"""Tests for the provider factory + fallback wiring (Milestone 4)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from screener.config import Settings
from screener.data.factory import (
    _MergedFundamentalsProvider,
    _MergedLosersUniverse,
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


async def test_build_universe_provider_merges_schwab_and_yfinance_when_schwab_succeeds(tmp_path):
    """Explicit user request ("use both, I actually want top 20"): a healthy
    Schwab result must ALSO always pull yfinance and union the two, not
    short-circuit yfinance the way the old pure-fallback wrapper did."""
    settings = _settings(tmp_path, universe="losers", data_provider="schwab")
    watchlist = {"AAPL"}

    mock_schwab_universe = MagicMock()
    mock_schwab_universe.get_symbols = AsyncMock(return_value=["AAPL"])
    # industry/sector already present (truthy) so the industry/sector
    # enrichment helper short-circuits without touching yfinance/network.
    mock_schwab_universe.get_quotes = AsyncMock(
        return_value={
            "AAPL": {
                "price_to_book": 1.0,
                "change_pct": -3.0,
                "industry": "Consumer Electronics",
                "sector": "Technology",
            }
        }
    )

    mock_yfinance_universe = MagicMock()
    mock_yfinance_universe.get_symbols = AsyncMock(return_value=["MSFT"])
    mock_yfinance_universe.get_quotes = AsyncMock(
        return_value={"MSFT": {"price_to_book": 2.0, "change_pct": -1.0, "industry": "Software", "sector": "Technology"}}
    )

    with patch("screener.data.factory.SchwabAuth"), \
         patch("screener.data.factory.SchwabClient"), \
         patch("screener.data.factory.SchwabLosersUniverse", return_value=mock_schwab_universe), \
         patch("screener.data.factory.LosersUniverse", return_value=mock_yfinance_universe), \
         patch("screener.data.factory.yf.Ticker") as mock_ticker_cls:
        provider = build_universe_provider(settings, watchlist)
        symbols = await provider.get_symbols()
        quotes = await provider.get_quotes()

    assert symbols == ["AAPL", "MSFT"]
    assert quotes.keys() == {"AAPL", "MSFT"}
    mock_yfinance_universe.get_symbols.assert_called_once()
    mock_yfinance_universe.get_quotes.assert_called_once()
    mock_ticker_cls.assert_not_called()


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


async def test_merged_losers_universe_get_quotes_routes_to_yfinance_after_fallback():
    """State-consistency: after get_symbols() degrades to yfinance-only
    (Schwab failure), get_quotes() must route to the yfinance provider's
    quotes, not stale/empty Schwab state."""
    mock_schwab_universe = MagicMock()
    mock_schwab_universe.get_symbols = AsyncMock(side_effect=RuntimeError("schwab down"))
    mock_schwab_universe.get_quotes = AsyncMock(return_value={"SHOULD_NOT_BE_USED": {}})

    mock_yfinance_universe = MagicMock()
    mock_yfinance_universe.get_symbols = AsyncMock(return_value=["NFLX"])
    mock_yfinance_universe.get_quotes = AsyncMock(return_value={"NFLX": {"price_to_book": 2.5}})

    merged = _MergedLosersUniverse(mock_schwab_universe, mock_yfinance_universe)

    symbols = await merged.get_symbols()
    quotes = await merged.get_quotes()

    assert symbols == ["NFLX"]
    assert quotes == {"NFLX": {"price_to_book": 2.5}}
    mock_schwab_universe.get_quotes.assert_not_called()


async def test_merged_losers_universe_get_symbols_falls_back_on_empty_schwab_result():
    """Schwab's own error handling swallows HTTP/auth failures and returns an
    empty list instead of raising, so the wrapper must also treat an empty
    (falsy) result as a fallback trigger, not just an exception."""
    mock_schwab_universe = MagicMock()
    mock_schwab_universe.get_symbols = AsyncMock(return_value=[])

    mock_yfinance_universe = MagicMock()
    mock_yfinance_universe.get_symbols = AsyncMock(return_value=["NFLX"])
    mock_yfinance_universe.get_quotes = AsyncMock(return_value={"NFLX": {"price_to_book": 2.5}})

    merged = _MergedLosersUniverse(mock_schwab_universe, mock_yfinance_universe)

    symbols = await merged.get_symbols()
    quotes = await merged.get_quotes()

    assert symbols == ["NFLX"]
    assert quotes == {"NFLX": {"price_to_book": 2.5}}


async def test_merged_losers_universe_unions_both_sources_preferring_schwab_on_overlap():
    """The core new behavior: Schwab succeeding must not exclude yfinance —
    both sources' quotes are unioned, and a symbol present in BOTH resolves
    to the Schwab-sourced entry (Schwab is primary elsewhere in this file)."""
    mock_schwab_universe = MagicMock()
    mock_schwab_universe.get_symbols = AsyncMock(return_value=["AAPL", "NVDA"])
    mock_schwab_universe.get_quotes = AsyncMock(
        return_value={
            "AAPL": {"price_to_book": 1.0, "change_pct": -2.0, "industry": None, "sector": None},
            "NVDA": {"price_to_book": 3.0, "change_pct": -5.0, "industry": None, "sector": None},
        }
    )

    mock_yfinance_universe = MagicMock()
    mock_yfinance_universe.get_symbols = AsyncMock(return_value=["AAPL", "MSFT"])
    mock_yfinance_universe.get_quotes = AsyncMock(
        return_value={
            # AAPL overlaps with Schwab — Schwab's version must win.
            "AAPL": {"price_to_book": 999.0, "change_pct": -999.0, "industry": "yfinance-only", "sector": "yfinance-only"},
            "MSFT": {"price_to_book": 2.0, "change_pct": -1.0, "industry": "Software", "sector": "Technology"},
        }
    )

    merged = _MergedLosersUniverse(mock_schwab_universe, mock_yfinance_universe)

    with patch("screener.data.factory.yf.Ticker") as mock_ticker_cls:
        mock_ticker_cls.return_value.info = {"industry": "Semiconductors", "sector": "Technology"}
        symbols = await merged.get_symbols()
        quotes = await merged.get_quotes()

    assert set(symbols) == {"AAPL", "NVDA", "MSFT"}
    # Schwab wins the AAPL conflict, not yfinance's 999.0 sentinel values.
    assert quotes["AAPL"]["price_to_book"] == 1.0
    assert quotes["AAPL"]["change_pct"] == -2.0
    # yfinance-only symbol carried through untouched.
    assert quotes["MSFT"] == {"price_to_book": 2.0, "change_pct": -1.0, "industry": "Software", "sector": "Technology"}
    # Schwab-sourced entries get industry/sector enrichment (originally None).
    assert quotes["NVDA"]["industry"] == "Semiconductors"
    assert quotes["NVDA"]["sector"] == "Technology"


async def test_merged_losers_universe_reranks_and_trims_to_top_20():
    """Once unioned, the combined dict is re-ranked by change_pct ascending
    (most negative/worst loss first) and sliced to the top 20 overall —
    not top-N-per-source."""
    # Schwab's 5 are the worst losses of the combined pool (-46..-50), so
    # all 5 always make the top 20. yfinance's 20 span -1..-20, so only its
    # 15 most-negative (-6..-20) survive the trim; its 5 least-negative
    # (-1..-5, i.e. YF0..YF4) are the ones that get dropped.
    schwab_quotes = {
        f"SCHWAB{i}": {"price_to_book": 1.0, "change_pct": -(i + 46.0), "industry": "X", "sector": "Y"}
        for i in range(5)
    }
    yfinance_quotes = {
        f"YF{i}": {"price_to_book": 1.0, "change_pct": -(i + 1.0), "industry": "X", "sector": "Y"}
        for i in range(20)
    }

    mock_schwab_universe = MagicMock()
    mock_schwab_universe.get_symbols = AsyncMock(return_value=list(schwab_quotes.keys()))
    mock_schwab_universe.get_quotes = AsyncMock(return_value=schwab_quotes)

    mock_yfinance_universe = MagicMock()
    mock_yfinance_universe.get_symbols = AsyncMock(return_value=list(yfinance_quotes.keys()))
    mock_yfinance_universe.get_quotes = AsyncMock(return_value=yfinance_quotes)

    merged = _MergedLosersUniverse(mock_schwab_universe, mock_yfinance_universe)

    symbols = await merged.get_symbols()
    quotes = await merged.get_quotes()

    assert len(symbols) == 20
    assert len(quotes) == 20
    assert set(f"SCHWAB{i}" for i in range(5)).issubset(set(symbols))
    dropped = {f"YF{i}" for i in range(5)}
    assert dropped.isdisjoint(set(symbols))
    assert set(symbols) == set(schwab_quotes.keys()) | {f"YF{i}" for i in range(5, 20)}


async def test_merged_losers_universe_quote_for_falls_back_on_none_schwab_result():
    """quote_for() must fall back to yfinance when Schwab returns None
    (no exception), matching the same empty/falsy-result contract as
    get_symbols(). This codepath is deliberately unchanged from the old
    pure-fallback wrapper — it's used by --ticker debug mode for an
    arbitrary symbol, not part of the get_symbols()/get_quotes() union."""
    mock_schwab_universe = MagicMock()
    mock_schwab_universe.quote_for = AsyncMock(return_value=None)

    mock_yfinance_universe = MagicMock()
    mock_yfinance_universe.quote_for = AsyncMock(return_value={"price_to_book": 2.5})

    merged = _MergedLosersUniverse(mock_schwab_universe, mock_yfinance_universe)

    quote = await merged.quote_for("NFLX")

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


async def test_merged_fundamentals_gross_and_net_margin_schwab_fields_populated():
    """interest_coverage is intentionally NOT in `_SCHWAB_FIELDS` (live
    testing found Schwab returns exactly 0.0 for both AAPL and MSFT — see
    `_MergedFundamentalsProvider`'s docstring), so it must always fall
    straight through from yfinance regardless of what Schwab returns."""
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

    assert result.gross_margin == 0.55
    assert result.net_margin == 0.33
    # yfinance remains the source of truth for these, including
    # interest_coverage (never merged from Schwab, even though Schwab
    # returned a non-None 99.0 here):
    assert result.interest_coverage == yfinance_snapshot.interest_coverage
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

    # interest_coverage is never merged (see _SCHWAB_FIELDS) — always
    # yfinance's value, regardless of what Schwab returned:
    assert result.interest_coverage == 10.0
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
# _MergedLosersUniverse industry/sector enrichment
# ---------------------------------------------------------------------------


async def test_get_symbols_enriches_schwab_sourced_quotes_missing_industry_sector():
    """Enrichment now happens inline during get_symbols()'s merge step
    (there's no more standalone "schwab active" get_quotes() fallback path
    to hang this off of) — drive it through the real get_symbols() call."""
    mock_schwab_universe = MagicMock()
    mock_schwab_universe.get_symbols = AsyncMock(return_value=["NVDA"])
    mock_schwab_universe.get_quotes = AsyncMock(
        return_value={"NVDA": {"price_to_book": 12.0, "change_pct": -3.0, "industry": None, "sector": None}}
    )

    mock_yfinance_universe = MagicMock()
    mock_yfinance_universe.get_symbols = AsyncMock(return_value=[])
    mock_yfinance_universe.get_quotes = AsyncMock(return_value={})

    merged = _MergedLosersUniverse(mock_schwab_universe, mock_yfinance_universe)

    mock_info = {"industry": "Semiconductors", "sector": "Technology"}
    with patch("screener.data.factory.yf.Ticker") as mock_ticker_cls:
        mock_ticker_cls.return_value.info = mock_info
        await merged.get_symbols()
        quotes = await merged.get_quotes()

    assert quotes["NVDA"]["industry"] == "Semiconductors"
    assert quotes["NVDA"]["sector"] == "Technology"
    mock_ticker_cls.assert_called_once_with("NVDA")


async def test_get_quotes_does_not_reenrich_yfinance_sourced_quotes_on_fallback():
    """In the degraded (Schwab-failed) fallback path, get_quotes() routes
    straight to yfinance's own quotes — no enrichment, since yfinance
    quotes already carry real industry/sector."""
    mock_schwab_universe = MagicMock()
    mock_yfinance_universe = MagicMock()
    mock_yfinance_universe.get_quotes = AsyncMock(
        return_value={"NFLX": {"price_to_book": 2.5, "industry": "Entertainment", "sector": "Comm"}}
    )

    merged = _MergedLosersUniverse(mock_schwab_universe, mock_yfinance_universe)
    merged._active = "yfinance"

    with patch("screener.data.factory.yf.Ticker") as mock_ticker_cls:
        quotes = await merged.get_quotes()

    assert quotes == {"NFLX": {"price_to_book": 2.5, "industry": "Entertainment", "sector": "Comm"}}
    mock_ticker_cls.assert_not_called()


async def test_quote_for_enriches_schwab_sourced_quote():
    mock_schwab_universe = MagicMock()
    mock_schwab_universe.quote_for = AsyncMock(
        return_value={"price_to_book": 12.0, "industry": None, "sector": None}
    )
    mock_yfinance_universe = MagicMock()

    merged = _MergedLosersUniverse(mock_schwab_universe, mock_yfinance_universe)

    mock_info = {"industry": "Semiconductors", "sector": "Technology"}
    with patch("screener.data.factory.yf.Ticker") as mock_ticker_cls:
        mock_ticker_cls.return_value.info = mock_info
        quote = await merged.quote_for("NVDA")

    assert quote["industry"] == "Semiconductors"
    assert quote["sector"] == "Technology"


async def test_quote_for_does_not_reenrich_yfinance_fallback_quote():
    mock_schwab_universe = MagicMock()
    mock_schwab_universe.quote_for = AsyncMock(return_value=None)
    mock_yfinance_universe = MagicMock()
    mock_yfinance_universe.quote_for = AsyncMock(
        return_value={"price_to_book": 2.5, "industry": "Entertainment", "sector": "Comm"}
    )

    merged = _MergedLosersUniverse(mock_schwab_universe, mock_yfinance_universe)

    with patch("screener.data.factory.yf.Ticker") as mock_ticker_cls:
        quote = await merged.quote_for("NFLX")

    assert quote == {"price_to_book": 2.5, "industry": "Entertainment", "sector": "Comm"}
    mock_ticker_cls.assert_not_called()
