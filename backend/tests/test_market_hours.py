"""Tests for the Schwab-backed pre-run market-hours gate."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from screener.config import Settings
from screener.data.schwab.market_hours import is_equity_market_open


def _settings(tmp_path, **overrides) -> Settings:
    defaults = dict(
        _env_file=None,
        data_provider="schwab",
        schwab_app_key="x",
        schwab_app_secret="x",
        schwab_callback_url="https://127.0.0.1:8182",
        schwab_token_path=str(tmp_path / "token.json"),
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _client_with_response(payload: dict) -> MagicMock:
    client = MagicMock()
    client.call = AsyncMock(return_value=payload)
    client.aclose = AsyncMock()
    # `client.raw.MarketHours.Market.EQUITY` is accessed inside the lambda
    # passed to `client.call` — since `call` is mocked and never actually
    # invokes the lambda, `client.raw` just needs to exist as a MagicMock
    # (it does, automatically) without raising.
    return client


async def test_non_schwab_provider_fails_open_without_touching_schwab(tmp_path):
    settings = _settings(tmp_path, data_provider="yfinance")

    with patch("screener.data.schwab.market_hours._build_schwab_auth") as mock_auth, \
         patch("screener.data.schwab.market_hours.SchwabClient") as mock_client_cls:
        result = await is_equity_market_open(settings)

    assert result is True
    mock_auth.assert_not_called()
    mock_client_cls.assert_not_called()


async def test_market_open_returns_true(tmp_path):
    settings = _settings(tmp_path)
    client = _client_with_response({"equity": {"EQ": {"isOpen": True}}})

    with patch("screener.data.schwab.market_hours._build_schwab_auth"), \
         patch("screener.data.schwab.market_hours.SchwabClient", return_value=client):
        result = await is_equity_market_open(settings)

    assert result is True
    client.aclose.assert_awaited_once()


async def test_market_closed_returns_false(tmp_path):
    settings = _settings(tmp_path)
    client = _client_with_response({"equity": {"EQ": {"isOpen": False}}})

    with patch("screener.data.schwab.market_hours._build_schwab_auth"), \
         patch("screener.data.schwab.market_hours.SchwabClient", return_value=client):
        result = await is_equity_market_open(settings)

    assert result is False
    client.aclose.assert_awaited_once()


async def test_client_call_exception_fails_open_and_logs_warning(tmp_path):
    settings = _settings(tmp_path)
    client = MagicMock()
    client.call = AsyncMock(side_effect=RuntimeError("boom"))
    client.aclose = AsyncMock()

    with patch("screener.data.schwab.market_hours._build_schwab_auth"), \
         patch("screener.data.schwab.market_hours.SchwabClient", return_value=client), \
         patch("screener.data.schwab.market_hours.logger.warning") as mock_warning:
        result = await is_equity_market_open(settings)

    assert result is True
    mock_warning.assert_called_once()
    assert mock_warning.call_args[0][0] == "market_hours_check_failed"
    client.aclose.assert_awaited_once()


async def test_malformed_response_fails_open(tmp_path):
    settings = _settings(tmp_path)
    client = _client_with_response({"equity": {"EQ": {"notIsOpen": "??"}}})

    with patch("screener.data.schwab.market_hours._build_schwab_auth"), \
         patch("screener.data.schwab.market_hours.SchwabClient", return_value=client), \
         patch("screener.data.schwab.market_hours.logger.warning") as mock_warning:
        result = await is_equity_market_open(settings)

    assert result is True
    mock_warning.assert_called_once()


async def test_auth_build_exception_fails_open(tmp_path):
    settings = _settings(tmp_path)

    with patch(
        "screener.data.schwab.market_hours._build_schwab_auth",
        side_effect=RuntimeError("no token"),
    ), patch("screener.data.schwab.market_hours.SchwabClient") as mock_client_cls, \
       patch("screener.data.schwab.market_hours.logger.warning") as mock_warning:
        result = await is_equity_market_open(settings)

    assert result is True
    mock_client_cls.assert_not_called()
    mock_warning.assert_called_once()
