"""Async, rate-limited wrapper around a schwab-py `AsyncClient` for the
Schwab Trader API.

schwab-py (via authlib's `AsyncOAuth2Client`) handles bearer-token injection
and proactive access-token refresh automatically at the transport level —
see `screener.data.schwab.auth`'s module docstring for the CONFIRMED source
citations backing that claim. What schwab-py does NOT provide, and what this
module adds back on top of it:

  - an async token-bucket rate limiter (~2 requests/sec, 120/min) so callers
    (including concurrent `asyncio.gather` fan-outs) never exceed Schwab's
    documented rate limit — confirmed absent from schwab-py's source;
  - a single bounded backoff+retry on HTTP 429 (respecting `Retry-After` if
    present) — also confirmed absent from both schwab-py and authlib.

Two call surfaces are exposed, both going through the same rate-limit/429-
retry machinery:

  - `get(path, params)` — legacy raw-path GET, preserved with its original
    signature/behavior for the 3 existing call sites in
    bars_provider.py/universe.py/fundamentals_provider.py. Internally calls
    the underlying AsyncClient's protected `_get_request(path, params)`
    method — schwab-py doesn't expose a public raw-path GET, and this is the
    narrowest available hook that reproduces the old client's "caller builds
    its own query params dict" shape without reimplementing URL/auth
    plumbing schwab-py already owns. New call sites should prefer `call()`
    below with one of schwab-py's typed methods instead of adding more
    `get()` callers.
  - `call(request_fn)` — generic wrapper for schwab-py's typed methods
    (`get_price_history`, `get_quotes`, `get_movers`, `get_market_hours`,
    ...). `request_fn` is a zero-arg callable returning the (unawaited)
    coroutine produced by calling a typed method on `self.raw`, e.g.:

        await client.call(lambda: client.raw.get_quotes(["AAPL", "MSFT"]))

    `call()` awaits that coroutine itself (after acquiring a rate-limit
    slot), applies the same bounded 429 retry as `get()`, then calls
    `.raise_for_status()` + `.json()` on the resulting `httpx.Response`.

401 handling — deliberately dropped: the previous hand-rolled client caught
a 401, fetched a fresh token, and retried once. That retry is now redundant
by construction: authlib's `AsyncOAuth2Client.request()` calls
`ensure_active_token()` BEFORE every request, proactively refreshing
whenever the token is within its leeway window of expiry (see
`screener.data.schwab.auth`'s docstring) — a 401 caused by an expired-but-
refreshable token should no longer be reachable through this client at all.
There is also no separate "force a fresh token and retry" affordance exposed
by schwab-py's `AsyncClient` to retry with, unlike the old design where
`SchwabAuth.get_access_token()` could simply be called again. So a 401 here
(e.g. a genuinely revoked token, or some other Schwab-side rejection) is
left to propagate via `raise_for_status()` as an `httpx.HTTPStatusError` —
callers' existing broad `except Exception` fallback logic
(`screener.data.factory`'s per-call schwab-then-yfinance wrappers) already
catches this. This is a considered design choice given schwab-py/authlib's
confirmed proactive-refresh behavior, not an oversight — if live-credential
testing (UNVERIFIED as of this writing) ever shows genuine reachable 401s,
reintroduce a bounded retry here.
"""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

import httpx

from screener.data.schwab.auth import SchwabAuth
from screener.utils.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_RATE_PER_SECOND = 2.0  # ~120 requests/minute
_DEFAULT_BUCKET_CAPACITY = 2.0
_DEFAULT_429_BACKOFF_SECONDS = 1.0


class SchwabAPIError(Exception):
    """Raised when a Schwab API request fails after exhausting the bounded
    429-retry budget: a repeated 429 after one backoff retry."""


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
    """Composed with a `SchwabAuth` instance (never subclasses it). Holds a
    lazily-constructed schwab-py `AsyncClient` (exposed as `.raw`) and issues
    rate-limited, 429-retried requests through it — either via the legacy
    `get(path, params)` surface or the generic typed-method `call()` surface.
    See the module docstring for the full design rationale."""

    def __init__(
        self,
        auth: SchwabAuth,
        base_url: str = "https://api.schwabapi.com",
    ) -> None:
        self._auth = auth
        self._base_url = base_url
        self._rate_limiter = _RateLimiter()
        self._raw = None

    @property
    def raw(self):
        """The underlying schwab-py `AsyncClient`, for callers that want to
        invoke typed methods directly (see `call()`). Constructed lazily —
        via `SchwabAuth.get_client()`, which never prompts interactively —
        on first access, and cached for the lifetime of this `SchwabClient`.
        """
        if self._raw is None:
            self._raw = self._auth.get_client()
        return self._raw

    async def aclose(self) -> None:
        """Close the underlying schwab-py `AsyncClient`'s HTTP session.
        Callers (and tests) should call this during teardown. A no-op if
        `.raw` was never accessed (nothing was ever constructed)."""
        if self._raw is not None:
            await self._raw.close_async_session()

    async def get(self, path: str, params: dict) -> dict:
        """Issue an authenticated, rate-limited GET to `path` with `params`,
        via the underlying schwab-py AsyncClient's protected raw-path
        request method. Retries at most once on 429 (respecting
        `Retry-After` if present). Raises `SchwabAPIError` if that retry
        budget is exhausted. See the module docstring for why 401s are no
        longer specially handled here, and for why this reaches into a
        protected schwab-py method.
        """
        response = await self._request_with_retry(
            lambda: self.raw._get_request(path, params), label=path
        )
        response.raise_for_status()
        return response.json()

    async def call(
        self, request_fn: Callable[[], Awaitable[httpx.Response]], *, label: str = "typed_call"
    ) -> dict:
        """Generic wrapper for schwab-py typed methods. `request_fn` is a
        zero-arg callable returning the coroutine from calling a typed
        method on `self.raw`, e.g.:

            await client.call(lambda: client.raw.get_quotes(["AAPL"]))

        Applies the same rate-limit + bounded 429-retry as `get()`, then
        `.raise_for_status()` + `.json()` on the response. `label` is purely
        for structured logging (e.g. pass the method name) — it plays no
        role in retry/rate-limit behavior.
        """
        response = await self._request_with_retry(request_fn, label=label)
        response.raise_for_status()
        return response.json()

    async def _request_with_retry(
        self, request_fn: Callable[[], Awaitable[httpx.Response]], *, label: str
    ) -> httpx.Response:
        """Shared rate-limit + bounded 429-retry machinery for both `get()`
        and `call()`. Does NOT call `raise_for_status()` — that's left to
        the caller, since `get()`/`call()` each need the response object a
        moment longer (to call `.json()`) before deciding to raise.
        """
        await self._rate_limiter.acquire()
        logger.info("schwab_request_start", path=label)
        response = await request_fn()

        if response.status_code == 429:
            retry_after_header = response.headers.get("Retry-After")
            try:
                wait_seconds = (
                    float(retry_after_header) if retry_after_header else _DEFAULT_429_BACKOFF_SECONDS
                )
            except ValueError:
                wait_seconds = _DEFAULT_429_BACKOFF_SECONDS
            logger.warning("schwab_429_backoff", path=label, wait_seconds=wait_seconds)
            await asyncio.sleep(wait_seconds)
            response = await request_fn()
            if response.status_code == 429:
                logger.error("schwab_429_retry_failed", path=label)
                raise SchwabAPIError(
                    f"Schwab API returned 429 twice for {label} — giving up after one retry."
                )

        return response
