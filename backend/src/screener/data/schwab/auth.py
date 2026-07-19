"""Schwab Trader API OAuth authentication, built on the `schwab-py` library
(https://github.com/alexgolec/schwab-py, PyPI `schwab-py`, import `schwab`)
rather than a hand-rolled OAuth implementation. `schwab-py` already
implements the self-signed-certificate loopback server needed to catch
Schwab's HTTPS redirect during the interactive browser flow (Schwab requires
an `https://` callback URL even for local loopback), so none of that plumbing
needs to be reimplemented here anymore — this module is now a thin wrapper
around two of `schwab.auth`'s entry points:

  1. `authorize()` — a one-time, interactive, sync CLI flow (invoked via
     `--auth-schwab`, never from an async request path). Calls
     `schwab.auth.client_from_login_flow()` directly (deliberately NOT the
     more convenient `schwab.auth.easy_client()` — see `authorize()`'s
     docstring for why) to force a fresh interactive re-authorization every
     time it's invoked, persisting the resulting token to `token_path`.
  2. `get_client()` — returns a live, ready-to-use
     `schwab.client.asynchronous.AsyncClient` for runtime (headless,
     `--once`/scheduler) use, by loading the existing token file via
     `schwab.auth.client_from_token_file()`. This NEVER opens a browser or
     blocks on user input — if `token_path` doesn't exist yet, or its
     contents can't be loaded as a valid token, this raises
     `SchwabAuthExpired` for the caller (and, in later milestones, the
     provider-factory fallback logic) to handle.

CONFIRMED (by reading the actually-installed schwab-py source in this
project's `.venv/lib/python3.12/site-packages/schwab/auth.py`, and authlib's
`AsyncOAuth2Client` in
`.venv/lib/python3.12/site-packages/authlib/integrations/httpx_client/oauth2_client.py`
— re-verify against whatever version `uv add schwab-py` installs if it
differs from schwab-py==1.5.1 / authlib==1.7.2):
  - Both `client_from_login_flow` and `client_from_token_file` return a
    `schwab.client.asynchronous.AsyncClient` when `asyncio=True`, wrapping an
    `authlib.integrations.httpx_client.AsyncOAuth2Client` session.
  - `client_from_login_flow` only accepts callback URLs whose host is
    literally `127.0.0.1` (raises `ValueError` otherwise) — matches this
    codebase's `schwab_callback_url` default of `https://127.0.0.1:8182`.
  - That authlib session's `request()` method calls `ensure_active_token()`
    before *every* outgoing request, which proactively refreshes the access
    token (using the stored refresh token) whenever it's within `leeway`
    (300s, set by schwab-py in `client_from_access_functions`) of expiry —
    and rewrites `token_path` on every refresh via the `update_token`
    callback schwab-py wires up. This means access-token refresh is now
    fully automatic at the transport level; nothing in this codebase needs
    to track `expires_at` or hit the refresh endpoint by hand anymore (this
    module used to do exactly that — see git history for the prior
    hand-rolled `get_access_token()`).
  - `client_from_token_file` (and the `client_from_access_functions` it
    delegates to) read the token file eagerly and let whatever exception
    that raises (`FileNotFoundError`, `json.JSONDecodeError`, etc.)
    propagate — `get_client()` below catches that and re-raises as
    `SchwabAuthExpired`, matching this module's previous behavior of
    treating "no usable token on disk" as one uniform condition.
  - schwab-py's session does NOT retry on HTTP 429 (confirmed absent from
    both schwab-py's and authlib's source) — Schwab's documented rate limit
    still applies, which is why `client.py`'s `_RateLimiter` and bounded
    429-retry are preserved unchanged in that module.

UNVERIFIED (no live Schwab credentials available this session — would
require them to exercise): the actual shape/success of a real browser OAuth
round-trip, and what a real dead (7-day-expired) refresh token's failure
mode looks like in practice when authlib's `ensure_active_token()` attempts
to use it mid-request.
"""
from __future__ import annotations

from pathlib import Path

import schwab.auth
from schwab.client.asynchronous import AsyncClient

from screener.utils.logging import get_logger

logger = get_logger(__name__)


class SchwabAuthExpired(Exception):
    """Raised when there is no valid way to obtain a Schwab-authenticated
    client without interactive re-authorization: either no token file exists
    yet (never authorized via `--auth-schwab`), or the stored token file
    exists but can't be loaded as a valid token (corrupt/unreadable JSON).
    Callers (including the fallback-provider logic in `screener.data.factory`,
    which already catches broad `Exception`) should treat this as "re-run
    --auth-schwab".

    A *live* refresh-token rejection from Schwab (the 7-day refresh token
    has actually died server-side) is a different case: that's now handled
    internally by schwab-py/authlib at request time (see the module
    docstring's CONFIRMED notes on `ensure_active_token()`), and is NOT
    wrapped into `SchwabAuthExpired` here — it will surface as whatever
    exception authlib raises from inside a request, which callers' existing
    broad `except Exception` fallback handling already covers.
    """


class SchwabAuth:
    """Thin wrapper around `schwab.auth`'s client-construction entry points.
    See the module docstring for the full design rationale."""

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        callback_url: str,
        token_path: str | Path,
    ) -> None:
        self._app_key = app_key
        self._app_secret = app_secret
        self._callback_url = callback_url
        self._token_path = Path(token_path)

    def authorize(self) -> None:
        """One-time interactive OAuth flow: delegates entirely to
        `schwab.auth.client_from_login_flow`, which opens the user's browser
        at Schwab's authorize URL, stands up its own temporary self-signed-
        cert loopback server to capture the redirect, exchanges the code for
        a token, and persists it to `token_path` — overwriting any existing
        token file there.

        Deliberately calls `client_from_login_flow` directly rather than the
        more convenient `schwab.auth.easy_client`: `easy_client` silently
        returns a client built from the EXISTING token file when one is
        already present at `token_path`, skipping the interactive flow
        entirely. That's exactly the right behavior for routine runtime
        client construction (which is what `get_client()` below does via
        `client_from_token_file` directly), but wrong for an explicit
        `--auth-schwab` command, whose entire purpose is to force a fresh
        interactive re-authorization on demand — a user who runs
        `--auth-schwab` expects a browser to open, not a silent no-op
        because a (possibly stale) token file happens to already exist.
        """
        logger.info("schwab_authorize_start", callback_url=self._callback_url)
        schwab.auth.client_from_login_flow(
            api_key=self._app_key,
            app_secret=self._app_secret,
            callback_url=self._callback_url,
            token_path=str(self._token_path),
            asyncio=True,
        )
        logger.info("schwab_authorize_complete", token_path=str(self._token_path))

    def get_client(self) -> AsyncClient:
        """Return a live `schwab.client.asynchronous.AsyncClient` for
        runtime use, loading the existing token from `token_path` via
        `schwab.auth.client_from_token_file`. NEVER prompts, opens a
        browser, or blocks on user input — safe to call from headless /
        scheduled code paths (this is the async-client equivalent of the
        old hand-rolled `get_access_token()`, but returns a whole configured
        client rather than a bearer-token string, since schwab-py's client
        handles token injection/refresh internally).

        Raises `SchwabAuthExpired` if `token_path` doesn't exist yet, or if
        its contents can't be loaded as a valid token (e.g. corrupt JSON) —
        in both cases the caller must re-run `--auth-schwab`.
        """
        if not self._token_path.exists():
            logger.warning("schwab_token_missing", token_path=str(self._token_path))
            raise SchwabAuthExpired(
                f"No Schwab token file at {self._token_path} — run --auth-schwab to authorize."
            )

        try:
            return schwab.auth.client_from_token_file(
                token_path=str(self._token_path),
                api_key=self._app_key,
                app_secret=self._app_secret,
                asyncio=True,
            )
        except Exception as exc:
            logger.warning(
                "schwab_token_load_failed",
                token_path=str(self._token_path),
                error=str(exc),
            )
            raise SchwabAuthExpired(
                f"Could not load Schwab token from {self._token_path} ({exc}) — "
                "run --auth-schwab to re-authorize."
            ) from exc
