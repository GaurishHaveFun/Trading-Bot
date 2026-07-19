"""Tests for `screener.main.run_screener`'s market-hours gating
(`check_market_hours` kwarg). Everything else in the pipeline is mocked out
— these tests only exercise the new gating behavior, not the full
universe/fetch/score/output pipeline (which has its own coverage
elsewhere)."""
from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

from screener.config import Settings
from screener.main import run_screener
from screener.models import ScreenerRun


def _settings(tmp_path, **overrides) -> Settings:
    defaults = dict(
        _env_file=None,
        data_provider="schwab",
        universe="static",
        schwab_app_key="x",
        schwab_app_secret="x",
        schwab_callback_url="https://127.0.0.1:8182",
        schwab_token_path=str(tmp_path / "token.json"),
        watchlist_path=str(tmp_path / "watchlist.yaml"),
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _mock_universe() -> MagicMock:
    universe = MagicMock()
    universe.get_symbols = AsyncMock(return_value=[])
    universe.get_quotes = AsyncMock(return_value={})
    return universe


def _rules_config() -> MagicMock:
    rules_config = MagicMock()
    rules_config.rules = []
    return rules_config


def _enter_pipeline_mocks(stack: ExitStack, tmp_path) -> dict:
    """Patches every collaborator `run_screener` touches (besides the
    market-hours gate) so the pipeline can run end-to-end cheaply against an
    empty universe (no symbols -> empty signals list -> still exercises
    write_run/write_report). Returns a dict of the started mocks, keyed by a
    short name, plus the `settings` instance used."""
    settings = _settings(tmp_path)
    written_run_path = tmp_path / "run.json"
    written_report_path = tmp_path / "report.pdf"

    targets = {
        "get_settings": patch("screener.main.get_settings", return_value=settings),
        "configure_logging": patch("screener.main.configure_logging"),
        "load_rules_config": patch("screener.main.load_rules_config", return_value=_rules_config()),
        "load_quality_screen_config": patch(
            "screener.main.load_quality_screen_config", return_value=MagicMock()
        ),
        "load_watchlist": patch("screener.main.load_watchlist", return_value=set()),
        "build_universe_provider": patch(
            "screener.main.build_universe_provider", return_value=_mock_universe()
        ),
        "BarCache": patch("screener.main.BarCache", return_value=MagicMock()),
        "build_bar_provider": patch("screener.main.build_bar_provider", return_value=MagicMock()),
        "FundamentalsCache": patch("screener.main.FundamentalsCache", return_value=MagicMock()),
        "build_fundamentals_provider": patch(
            "screener.main.build_fundamentals_provider", return_value=MagicMock()
        ),
        "RuleEngine": patch("screener.main.RuleEngine", return_value=MagicMock()),
        "write_run": patch("screener.main.write_run", return_value=written_run_path),
        "write_report": patch("screener.main.write_report", return_value=written_report_path),
    }

    mocks = {name: stack.enter_context(p) for name, p in targets.items()}
    mocks["settings"] = settings
    return mocks


async def test_check_market_hours_false_never_calls_is_equity_market_open(tmp_path):
    """Default behavior (`check_market_hours=False`, the pre-existing
    default for every manual invocation) must be a zero-behavior-change
    path: the market-hours helper is never imported/called, and the run
    proceeds through to completion."""
    with ExitStack() as stack:
        mocks = _enter_pipeline_mocks(stack, tmp_path)
        mock_is_open = stack.enter_context(
            patch("screener.data.schwab.market_hours.is_equity_market_open", new_callable=AsyncMock)
        )
        result = await run_screener()

    mock_is_open.assert_not_called()
    mocks["write_run"].assert_called_once()
    mocks["write_report"].assert_called_once()
    assert isinstance(result, ScreenerRun)


async def test_check_market_hours_true_and_market_closed_skips_run(tmp_path):
    """When gated and the market is closed, `run_screener` must return
    `None` early WITHOUT ever calling write_run/write_report or building any
    provider."""
    with ExitStack() as stack:
        mocks = _enter_pipeline_mocks(stack, tmp_path)
        mock_is_open = stack.enter_context(
            patch(
                "screener.data.schwab.market_hours.is_equity_market_open",
                new_callable=AsyncMock,
                return_value=False,
            )
        )
        result = await run_screener(check_market_hours=True)

    mock_is_open.assert_called_once_with(mocks["settings"])
    assert result is None
    mocks["write_run"].assert_not_called()
    mocks["write_report"].assert_not_called()
    mocks["build_universe_provider"].assert_not_called()


async def test_check_market_hours_true_and_market_open_runs_normally(tmp_path):
    """When gated and the market is open, the pipeline proceeds exactly as
    before and returns a `ScreenerRun`."""
    with ExitStack() as stack:
        mocks = _enter_pipeline_mocks(stack, tmp_path)
        mock_is_open = stack.enter_context(
            patch(
                "screener.data.schwab.market_hours.is_equity_market_open",
                new_callable=AsyncMock,
                return_value=True,
            )
        )
        result = await run_screener(check_market_hours=True)

    mock_is_open.assert_called_once_with(mocks["settings"])
    mocks["write_run"].assert_called_once()
    mocks["write_report"].assert_called_once()
    assert isinstance(result, ScreenerRun)
