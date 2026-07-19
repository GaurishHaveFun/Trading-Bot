"""Tests for SchwabClient, the rate-limited wrapper around a schwab-py
`AsyncClient` (see `screener/data/schwab/client.py`'s module docstring for
the full design rationale, including why 401-handling was deliberately
dropped in favor of authlib's proactive token refresh).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from screener.data.schwab.client import SchwabAPIError, SchwabClient, _RateLimiter


class _MockResponse:
    def __init__(self, status_code: int, json_data: dict | None = None, headers: dict | None = None) -> None:
        self.status_code = status_code
        self._json_data = json_data or {}
        self.headers = headers or {}

    def json(self) -> dict:
        return self._json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=MagicMock(), response=self
            )


@pytest.fixture
def mock_raw():
    """Stand-in for the schwab-py AsyncClient (`SchwabClient.raw`)."""
    return MagicMock(name="AsyncClient")


@pytest.fixture
def mock_auth(mock_raw):
    auth = MagicMock()
    auth.get_client.return_value = mock_raw
    return auth


@pytest.fixture
def client(mock_auth):
    return SchwabClient(auth=mock_auth)


# ---------------------------------------------------------------------------
# Rate limiter (unchanged behavior from the hand-rolled client)
# ---------------------------------------------------------------------------


def test_rate_limiter_allows_immediate_request_when_tokens_available():
    limiter = _RateLimiter(rate=2.0, capacity=2.0)
    wait = limiter._compute_wait()
    assert wait == 0.0
    assert limiter._tokens == pytest.approx(1.0)


def test_rate_limiter_computes_wait_when_tokens_exhausted():
    limiter = _RateLimiter(rate=2.0, capacity=2.0)
    limiter._tokens = 0.0

    from unittest.mock import patch
    with patch("screener.data.schwab.client.time.monotonic", return_value=limiter._last_refill):
        wait = limiter._compute_wait()

    # deficit=1.0 tokens at rate=2.0/sec -> 0.5s wait
    assert wait == pytest.approx(0.5)
    assert limiter._tokens == pytest.approx(0.0)


async def test_rate_limiter_acquire_sleeps_computed_wait():
    limiter = _RateLimiter(rate=2.0, capacity=2.0)
    limiter._tokens = 0.0

    from unittest.mock import patch
    with patch("screener.data.schwab.client.time.monotonic", return_value=limiter._last_refill), \
         patch("screener.data.schwab.client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await limiter.acquire()

    mock_sleep.assert_awaited_once()
    (awaited_wait,), _ = mock_sleep.call_args
    assert awaited_wait == pytest.approx(0.5)


async def test_rate_limiter_acquire_does_not_sleep_when_token_available():
    limiter = _RateLimiter(rate=2.0, capacity=2.0)

    from unittest.mock import patch
    with patch("screener.data.schwab.client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await limiter.acquire()

    mock_sleep.assert_not_awaited()


# ---------------------------------------------------------------------------
# .raw — lazy construction, never prompts interactively
# ---------------------------------------------------------------------------


def test_raw_lazily_constructs_client_via_auth_get_client(client, mock_auth, mock_raw):
    mock_auth.get_client.assert_not_called()

    result = client.raw

    assert result is mock_raw
    mock_auth.get_client.assert_called_once()


def test_raw_is_cached_across_accesses(client, mock_auth):
    _ = client.raw
    _ = client.raw

    mock_auth.get_client.assert_called_once()


# ---------------------------------------------------------------------------
# get() — legacy raw-path GET
# ---------------------------------------------------------------------------


async def test_get_calls_raw_get_request_and_returns_json(client, mock_raw):
    mock_response = _MockResponse(200, {"candles": []})
    mock_raw._get_request = AsyncMock(return_value=mock_response)

    result = await client.get("/marketdata/v1/pricehistory", {"symbol": "AAPL"})

    assert result == {"candles": []}
    mock_raw._get_request.assert_awaited_once_with(
        "/marketdata/v1/pricehistory", {"symbol": "AAPL"}
    )


async def test_get_raises_on_non_429_error_status(client, mock_raw):
    """A non-429 error status (e.g. a 401 that authlib's proactive refresh
    didn't prevent) is no longer specially retried — it propagates via
    raise_for_status() so callers' broad except-Exception fallback logic
    still catches it."""
    mock_raw._get_request = AsyncMock(return_value=_MockResponse(401))

    with pytest.raises(httpx.HTTPStatusError):
        await client.get("/marketdata/v1/pricehistory", {"symbol": "AAPL"})

    mock_raw._get_request.assert_awaited_once()


# ---------------------------------------------------------------------------
# call() — generic typed-method wrapper
# ---------------------------------------------------------------------------


async def test_call_awaits_request_fn_and_returns_json(client):
    mock_response = _MockResponse(200, {"quote": "data"})
    request_fn = AsyncMock(return_value=mock_response)

    result = await client.call(request_fn)

    assert result == {"quote": "data"}
    request_fn.assert_awaited_once()


async def test_call_does_not_touch_raw_directly(client, mock_auth):
    """call() just awaits whatever request_fn returns — it doesn't need to
    reach into .raw itself, so a request_fn that closes over a client's
    .raw (as real callers will do) shouldn't cause a second construction."""
    mock_response = _MockResponse(200, {})
    request_fn = AsyncMock(return_value=mock_response)

    await client.call(request_fn)

    mock_auth.get_client.assert_not_called()


# ---------------------------------------------------------------------------
# 429 backoff + retry — shared by get() and call()
# ---------------------------------------------------------------------------


async def test_429_with_retry_after_header_waits_and_retries(client, mock_raw):
    responses = [
        _MockResponse(429, headers={"Retry-After": "3"}),
        _MockResponse(200, {"candles": []}),
    ]
    mock_raw._get_request = AsyncMock(side_effect=responses)

    from unittest.mock import patch
    with patch("screener.data.schwab.client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await client.get("/marketdata/v1/pricehistory", {"symbol": "AAPL"})

    assert result == {"candles": []}
    assert mock_raw._get_request.await_count == 2
    mock_sleep.assert_awaited_once_with(3.0)


async def test_429_repeated_past_retry_cap_raises(client, mock_raw):
    responses = [
        _MockResponse(429, headers={"Retry-After": "1"}),
        _MockResponse(429, headers={"Retry-After": "1"}),
    ]
    mock_raw._get_request = AsyncMock(side_effect=responses)

    from unittest.mock import patch
    with patch("screener.data.schwab.client.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(SchwabAPIError):
            await client.get("/marketdata/v1/pricehistory", {"symbol": "AAPL"})

    assert mock_raw._get_request.await_count == 2


async def test_429_without_retry_after_header_uses_fixed_backoff(client, mock_raw):
    responses = [
        _MockResponse(429),
        _MockResponse(200, {"candles": []}),
    ]
    mock_raw._get_request = AsyncMock(side_effect=responses)

    from unittest.mock import patch
    with patch("screener.data.schwab.client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await client.get("/marketdata/v1/pricehistory", {"symbol": "AAPL"})

    assert result == {"candles": []}
    mock_sleep.assert_awaited_once_with(1.0)


async def test_call_retries_on_429_too(client):
    responses = [
        _MockResponse(429),
        _MockResponse(200, {"movers": []}),
    ]
    request_fn = AsyncMock(side_effect=responses)

    from unittest.mock import patch
    with patch("screener.data.schwab.client.asyncio.sleep", new_callable=AsyncMock):
        result = await client.call(request_fn)

    assert result == {"movers": []}
    assert request_fn.await_count == 2


# ---------------------------------------------------------------------------
# aclose()
# ---------------------------------------------------------------------------


async def test_aclose_closes_raw_session_if_constructed(client, mock_raw):
    mock_raw.close_async_session = AsyncMock()

    _ = client.raw  # force construction
    await client.aclose()

    mock_raw.close_async_session.assert_awaited_once()


async def test_aclose_is_noop_if_raw_never_constructed(client, mock_auth):
    await client.aclose()

    mock_auth.get_client.assert_not_called()
