"""Tests for SchwabAuth, the thin wrapper around schwab-py's `schwab.auth`
entry points (see `screener/data/schwab/auth.py`'s module docstring for the
full design rationale and CONFIRMED/UNVERIFIED notes).

Mocks at the schwab-py boundary (`schwab.auth.client_from_login_flow` /
`schwab.auth.client_from_token_file`) rather than raw httpx/socket/TLS
plumbing, since this module no longer touches any of that directly — schwab-py
owns it now.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from screener.data.schwab.auth import SchwabAuth, SchwabAuthExpired

APP_KEY = "test-app-key"
APP_SECRET = "test-app-secret"
CALLBACK_URL = "https://127.0.0.1:8182"


@pytest.fixture
def token_path(tmp_path):
    return tmp_path / "schwab_token.json"


@pytest.fixture
def auth(token_path):
    return SchwabAuth(
        app_key=APP_KEY,
        app_secret=APP_SECRET,
        callback_url=CALLBACK_URL,
        token_path=token_path,
    )


# ---------------------------------------------------------------------------
# get_client() — no-interactive-prompt runtime construction
# ---------------------------------------------------------------------------


def test_get_client_missing_token_file_raises_expired(auth):
    """No token file yet (never ran --auth-schwab) must raise
    SchwabAuthExpired without ever touching schwab.auth."""
    with patch("screener.data.schwab.auth.schwab.auth.client_from_token_file") as mock_load:
        with pytest.raises(SchwabAuthExpired):
            auth.get_client()
    mock_load.assert_not_called()


def test_get_client_loads_existing_token_without_prompting(auth, token_path):
    """An existing token file must be loaded via
    schwab.auth.client_from_token_file — never via the interactive
    client_from_login_flow — and the resulting client returned as-is."""
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text("{}")  # presence is all get_client() checks itself

    fake_client = MagicMock(name="AsyncClient")
    with patch(
        "screener.data.schwab.auth.schwab.auth.client_from_token_file",
        return_value=fake_client,
    ) as mock_load, patch(
        "screener.data.schwab.auth.schwab.auth.client_from_login_flow"
    ) as mock_login_flow:
        client = auth.get_client()

    assert client is fake_client
    mock_login_flow.assert_not_called()
    mock_load.assert_called_once_with(
        token_path=str(token_path),
        api_key=APP_KEY,
        app_secret=APP_SECRET,
        asyncio=True,
    )


def test_get_client_wraps_load_failure_as_expired(auth, token_path):
    """A token file that exists but fails to load (corrupt JSON, schwab-py
    rejecting its contents, etc.) must be surfaced as SchwabAuthExpired, not
    the raw underlying exception."""
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text("not valid json")

    with patch(
        "screener.data.schwab.auth.schwab.auth.client_from_token_file",
        side_effect=ValueError("bad token"),
    ):
        with pytest.raises(SchwabAuthExpired):
            auth.get_client()


# ---------------------------------------------------------------------------
# authorize() — interactive flow
# ---------------------------------------------------------------------------


def test_authorize_calls_client_from_login_flow(auth, token_path):
    """authorize() must delegate to schwab.auth.client_from_login_flow with
    this SchwabAuth's credentials/callback/token_path, forcing the
    interactive flow every time (never client_from_token_file / easy_client,
    which could silently skip re-authorization if a token already exists)."""
    with patch(
        "screener.data.schwab.auth.schwab.auth.client_from_login_flow"
    ) as mock_login_flow, patch(
        "screener.data.schwab.auth.schwab.auth.client_from_token_file"
    ) as mock_load:
        auth.authorize()

    mock_load.assert_not_called()
    mock_login_flow.assert_called_once_with(
        api_key=APP_KEY,
        app_secret=APP_SECRET,
        callback_url=CALLBACK_URL,
        token_path=str(token_path),
        asyncio=True,
    )


def test_authorize_propagates_login_flow_errors(auth):
    """If the interactive flow itself fails (e.g. the user never completes
    the browser redirect), authorize() must not swallow that error."""
    with patch(
        "screener.data.schwab.auth.schwab.auth.client_from_login_flow",
        side_effect=RuntimeError("no callback received"),
    ):
        with pytest.raises(RuntimeError):
            auth.authorize()


@pytest.mark.integration
def test_real_authorize_flow():
    """Live flow — opens a real browser and drives schwab-py's real loopback
    server against the real Schwab OAuth endpoints. Skipped in CI with
    -m 'not integration'. Requires SCHWAB_APP_KEY/SCHWAB_APP_SECRET to be
    configured and a human present to complete the browser login."""
    import tempfile

    from screener.config import get_settings

    settings = get_settings()
    if not settings.schwab_app_key:
        pytest.skip("SCHWAB_APP_KEY not configured")

    with tempfile.TemporaryDirectory() as tmp:
        token_path = f"{tmp}/schwab_token.json"
        auth = SchwabAuth(
            app_key=settings.schwab_app_key,
            app_secret=settings.schwab_app_secret,
            callback_url=settings.schwab_callback_url,
            token_path=token_path,
        )
        auth.authorize()
        client = auth.get_client()
        assert client is not None
