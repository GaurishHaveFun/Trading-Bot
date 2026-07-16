"""Thin async HTTP client for the Schwab Trader API (Milestone 2).

Wraps a shared `httpx.AsyncClient` with:
  - an async token-bucket rate limiter (~2 requests/sec, 120/min) so callers
    (including concurrent `asyncio.gather` fan-outs) never exceed Schwab's
    documented rate limit;
  - bearer-token injection via `SchwabAuth.get_access_token()` (Milestone 1),
    wrapped in `asyncio.to_thread` since `SchwabAuth`'s methods are sync and
    may do blocking network I/O internally;
  - a single bounded refresh+retry on HTTP 401, and a single bounded
    backoff+retry on HTTP 429 — never an unbounded retry loop.

`SchwabAuthExpired` (raised by `SchwabAuth.get_access_token()` when there is
no valid way to obtain a token without interactive re-authorization) is
never caught here — it propagates straight out of `get()` to the caller.
"""
from __future__ import annotations

import asyncio
import time

import httpx

from screener.data.schwab.auth import SchwabAuth
from screener.utils.logging import get_logger

logger = get_logger(__name__)

_HTTP_TIMEOUT_SECONDS = 30.0
_DEFAULT_RATE_PER_SECOND = 2.0  # ~120 requests/minute
_DEFAULT_BUCKET_CAPACITY = 2.0
_DEFAULT_429_BACKOFF_SECONDS = 1.0


class SchwabAPIError(Exception):
    """Raised when a Schwab API request fails after exhausting the bounded
    retry budget: a repeated 401 after one token-refresh retry, or a
    repeated 429 after one backoff retry."""


class _RateLimiter:
    """Async token-bucket rate limiter enforcing ~`rate` requests/sec.

    The core throttling decision — "given the current token count and time
    since last refill, how long must the caller wait, and what's the new
    state?" — lives in `_compute_wait`, a small synchronous method that
    mutates `_tokens`/`_last_refill` and returns the wait in seconds. Tests
    can call it directly (optionally after poking `_tokens`/`_last_refill`
    manually) without touching real wall-clock time, or patch `asyncio.sleep`
    and assert it was awaited with the value `_compute_wait` returned.

    `_lock` guards all reads/writes of `_tokens`/`_last_refill` so concurrent
    `acquire()` callers (e.g. via `asyncio.gather`) each get a distinct,
    correctly-accounted-for reservation rather than racing on the same
    token count.
    """

    def __init__(
        self,
        rate: float = _DEFAULT_RATE_PER_SECOND,
        capacity: float = _DEFAULT_BUCKET_CAPACITY,
    ) -> None:
        self._rate = rate
        self._capacity = capacity
        self._tokens = capacity
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Block (via `asyncio.sleep`) until a token is available, then
        consume it. Safe to call concurrently."""
        async with self._lock:
            wait = self._compute_wait()
        if wait > 0:
            logger.info("schwab_rate_limit_wait", wait_seconds=wait)
            await asyncio.sleep(wait)

    def _compute_wait(self) -> float:
        """Refill tokens for elapsed time, then reserve one token for the
        caller. Returns 0.0 if a token was immediately available, otherwise
        the number of seconds the caller must sleep before proceeding (the
        token is reserved up front so a subsequent concurrent caller sees
        the updated, lower token count rather than racing on a stale read).

        Must be called with `_lock` held.
        """
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now

        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return 0.0

        deficit = 1.0 - self._tokens
        wait = deficit / self._rate
        self._tokens = 0.0
        return wait


class SchwabClient:
    """Composed with a `SchwabAuth` instance (never subclasses it) to issue
    authenticated, rate-limited GET requests against the Schwab Trader API."""

    def __init__(
        self,
        auth: SchwabAuth,
        base_url: str = "https://api.schwabapi.com",
    ) -> None:
        self._auth = auth
        self._base_url = base_url
        self._rate_limiter = _RateLimiter()
        self._client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS)

    async def aclose(self) -> None:
        """Close the shared underlying `httpx.AsyncClient`. Callers (and
        tests) should call this during teardown."""
        await self._client.aclose()

    async def get(self, path: str, params: dict) -> dict:
        """Issue an authenticated, rate-limited GET to `self._base_url + path`.

        Retries at most once on 401 (fresh token) and at most once on 429
        (respecting `Retry-After` if present). Raises `SchwabAPIError` if
        either retry budget is exhausted. `SchwabAuthExpired` from
        `self._auth.get_access_token()` propagates uncaught.
        """
        await self._rate_limiter.acquire()

        url = f"{self._base_url}{path}"
        token = await asyncio.to_thread(self._auth.get_access_token)
        logger.info("schwab_request_start", path=path)
        response = await self._do_request(url, params, token)

        if response.status_code == 401:
            logger.warning("schwab_401_retry", path=path)
            token = await asyncio.to_thread(self._auth.get_access_token)
            response = await self._do_request(url, params, token)
            if response.status_code == 401:
                logger.error("schwab_401_retry_failed", path=path)
                raise SchwabAPIError(
                    f"Schwab API returned 401 twice for {path} — giving up after one retry."
                )

        if response.status_code == 429:
            retry_after_header = response.headers.get("Retry-After")
            try:
                wait_seconds = float(retry_after_header) if retry_after_header else _DEFAULT_429_BACKOFF_SECONDS
            except ValueError:
                wait_seconds = _DEFAULT_429_BACKOFF_SECONDS
            logger.warning("schwab_429_backoff", path=path, wait_seconds=wait_seconds)
            await asyncio.sleep(wait_seconds)
            token = await asyncio.to_thread(self._auth.get_access_token)
            response = await self._do_request(url, params, token)
            if response.status_code == 429:
                logger.error("schwab_429_retry_failed", path=path)
                raise SchwabAPIError(
                    f"Schwab API returned 429 twice for {path} — giving up after one retry."
                )

        response.raise_for_status()
        return response.json()

    async def _do_request(self, url: str, params: dict, token: str) -> httpx.Response:
        return await self._client.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
