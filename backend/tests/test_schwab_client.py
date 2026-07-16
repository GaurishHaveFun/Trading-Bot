"""Tests for SchwabClient (Milestone 2: rate-limited HTTP client)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from screener.data.schwab.auth import SchwabAuthExpired
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
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def mock_auth():
    auth = MagicMock()
    auth.get_access_token.return_value = "test-access-token"
    return auth


@pytest.fixture
def client(mock_auth):
    with patch("screener.data.schwab.client.httpx.AsyncClient"):
        c = SchwabClient(auth=mock_auth)
    return c


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


def test_rate_limiter_allows_immediate_request_when_tokens_available():
    limiter = _RateLimiter(rate=2.0, capacity=2.0)
    wait = limiter._compute_wait()
    assert wait == 0.0
    assert limiter._tokens == pytest.approx(1.0)


def test_rate_limiter_computes_wait_when_tokens_exhausted():
    limiter = _RateLimiter(rate=2.0, capacity=2.0)
    # Drain the bucket and pin last_refill to "now" so no refill has happened.
    limiter._tokens = 0.0
    limiter._last_refill = limiter._last_refill  # no-op, just documenting intent

    import time
    with patch("screener.data.schwab.client.time.monotonic", return_value=limiter._last_refill):
        wait = limiter._compute_wait()

    # deficit=1.0 tokens at rate=2.0/sec -> 0.5s wait
    assert wait == pytest.approx(0.5)
    assert limiter._tokens == pytest.approx(0.0)


async def test_rate_limiter_acquire_sleeps_computed_wait():
    limiter = _RateLimiter(rate=2.0, capacity=2.0)
    limiter._tokens = 0.0

    with patch("screener.data.schwab.client.time.monotonic", return_value=limiter._last_refill), \
         patch("screener.data.schwab.client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await limiter.acquire()

    mock_sleep.assert_awaited_once()
    (awaited_wait,), _ = mock_sleep.call_args
    assert awaited_wait == pytest.approx(0.5)


async def test_rate_limiter_acquire_does_not_sleep_when_token_available():
    limiter = _RateLimiter(rate=2.0, capacity=2.0)

    with patch("screener.data.schwab.client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await limiter.acquire()

    mock_sleep.assert_not_awaited()


# ---------------------------------------------------------------------------
# get() — token injection
# ---------------------------------------------------------------------------


async def test_get_injects_bearer_token_into_authorization_header(client, mock_auth):
    mock_response = _MockResponse(200, {"candles": []})
    client._client.get = AsyncMock(return_value=mock_response)

    result = await client.get("/marketdata/v1/pricehistory", {"symbol": "AAPL"})

    assert result == {"candles": []}
    mock_auth.get_access_token.assert_called_once()
    _, call_kwargs = client._client.get.call_args
    assert call_kwargs["headers"]["Authorization"] == "Bearer test-access-token"


# ---------------------------------------------------------------------------
# get() — 401 refresh + retry
# ---------------------------------------------------------------------------


async def test_401_triggers_single_refresh_and_retry_then_succeeds(client, mock_auth):
    mock_auth.get_access_token.side_effect = ["stale-token", "fresh-token"]
    responses = [_MockResponse(401), _MockResponse(200, {"candles": []})]
    client._client.get = AsyncMock(side_effect=responses)

    result = await client.get("/marketdata/v1/pricehistory", {"symbol": "AAPL"})

    assert result == {"candles": []}
    assert mock_auth.get_access_token.call_count == 2
    assert client._client.get.call_count == 2
    # second call must use the refreshed token
    _, second_kwargs = client._client.get.call_args_list[1]
    assert second_kwargs["headers"]["Authorization"] == "Bearer fresh-token"


async def test_401_twice_raises_schwab_api_error(client, mock_auth):
    mock_auth.get_access_token.side_effect = ["stale-token", "still-stale-token"]
    responses = [_MockResponse(401), _MockResponse(401)]
    client._client.get = AsyncMock(side_effect=responses)

    with pytest.raises(SchwabAPIError):
        await client.get("/marketdata/v1/pricehistory", {"symbol": "AAPL"})

    assert mock_auth.get_access_token.call_count == 2
    assert client._client.get.call_count == 2


# ---------------------------------------------------------------------------
# get() — 429 backoff + retry
# ---------------------------------------------------------------------------


async def test_429_with_retry_after_header_waits_and_retries(client, mock_auth):
    responses = [
        _MockResponse(429, headers={"Retry-After": "3"}),
        _MockResponse(200, {"candles": []}),
    ]
    client._client.get = AsyncMock(side_effect=responses)

    with patch("screener.data.schwab.client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await client.get("/marketdata/v1/pricehistory", {"symbol": "AAPL"})

    assert result == {"candles": []}
    assert client._client.get.call_count == 2
    mock_sleep.assert_awaited_once_with(3.0)


async def test_429_repeated_past_retry_cap_raises(client, mock_auth):
    responses = [
        _MockResponse(429, headers={"Retry-After": "1"}),
        _MockResponse(429, headers={"Retry-After": "1"}),
    ]
    client._client.get = AsyncMock(side_effect=responses)

    with patch("screener.data.schwab.client.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(SchwabAPIError):
            await client.get("/marketdata/v1/pricehistory", {"symbol": "AAPL"})

    assert client._client.get.call_count == 2


async def test_429_without_retry_after_header_uses_fixed_backoff(client, mock_auth):
    responses = [
        _MockResponse(429),
        _MockResponse(200, {"candles": []}),
    ]
    client._client.get = AsyncMock(side_effect=responses)

    with patch("screener.data.schwab.client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await client.get("/marketdata/v1/pricehistory", {"symbol": "AAPL"})

    assert result == {"candles": []}
    mock_sleep.assert_awaited_once_with(1.0)


# ---------------------------------------------------------------------------
# get() — SchwabAuthExpired propagation
# ---------------------------------------------------------------------------


async def test_schwab_auth_expired_propagates_uncaught(client, mock_auth):
    mock_auth.get_access_token.side_effect = SchwabAuthExpired("no valid token")
    client._client.get = AsyncMock()

    with pytest.raises(SchwabAuthExpired):
        await client.get("/marketdata/v1/pricehistory", {"symbol": "AAPL"})

    client._client.get.assert_not_called()


# ---------------------------------------------------------------------------
# aclose()
# ---------------------------------------------------------------------------


async def test_aclose_closes_underlying_httpx_client(client):
    client._client.aclose = AsyncMock()
    await client.aclose()
    client._client.aclose.assert_awaited_once()
