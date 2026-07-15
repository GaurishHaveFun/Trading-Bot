"""Tests for SchwabAuth (Milestone 1: config + OAuth auth foundation)."""
from __future__ import annotations

import json
import os
import ssl
import stat
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from screener.data.schwab.auth import SchwabAuth, SchwabAuthExpired, _generate_self_signed_cert

APP_KEY = "test-app-key"
APP_SECRET = "test-app-secret"
CALLBACK_URL = "https://127.0.0.1:8182"


class _MockResponse:
    def __init__(self, status_code: int, json_data: dict) -> None:
        self.status_code = status_code
        self._json_data = json_data

    def json(self) -> dict:
        return self._json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _write_token_file(path, *, expires_at: datetime, access_token: str = "cached-access-token",
                       refresh_token: str = "cached-refresh-token") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    token_data = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    path.write_text(json.dumps(token_data))


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


def test_missing_token_file_raises_expired(auth):
    """No token file yet (never ran --auth-schwab) must raise SchwabAuthExpired."""
    with pytest.raises(SchwabAuthExpired):
        auth.get_access_token()


def test_returns_cached_token_without_refresh(auth, token_path):
    """A token with plenty of time left must be returned directly, with no
    network call at all."""
    far_future = datetime.now(timezone.utc) + timedelta(minutes=25)
    _write_token_file(token_path, expires_at=far_future, access_token="still-good-token")

    with patch("screener.data.schwab.auth.httpx.Client") as mock_client_cls:
        token = auth.get_access_token()

    mock_client_cls.assert_not_called()
    assert token == "still-good-token"


def test_refresh_triggered_when_near_expiry(auth, token_path):
    """A token past (or within 60s of) expiry must trigger a refresh POST,
    and the new token must be persisted to disk and returned."""
    past = datetime.now(timezone.utc) - timedelta(seconds=5)
    _write_token_file(token_path, expires_at=past, refresh_token="old-refresh-token")

    fresh_payload = {
        "access_token": "new-access-token",
        "refresh_token": "new-refresh-token",
        "expires_in": 1800,
    }
    mock_client = MagicMock()
    mock_client.__enter__.return_value.post.return_value = _MockResponse(200, fresh_payload)

    with patch("screener.data.schwab.auth.httpx.Client", return_value=mock_client):
        token = auth.get_access_token()

    assert token == "new-access-token"

    persisted = json.loads(token_path.read_text())
    assert persisted["access_token"] == "new-access-token"
    assert persisted["refresh_token"] == "new-refresh-token"
    assert "issued_at" in persisted
    assert "expires_at" in persisted
    # expires_at must be UTC-parseable and in the future relative to issued_at
    issued = datetime.fromisoformat(persisted["issued_at"])
    expires = datetime.fromisoformat(persisted["expires_at"])
    assert expires > issued


def test_refresh_rejected_raises_expired(auth, token_path):
    """A 400 from the refresh POST (dead 7-day refresh token) must raise
    SchwabAuthExpired, not propagate the raw HTTP error."""
    past = datetime.now(timezone.utc) - timedelta(seconds=5)
    _write_token_file(token_path, expires_at=past)

    mock_client = MagicMock()
    mock_client.__enter__.return_value.post.return_value = _MockResponse(400, {"error": "invalid_grant"})

    with patch("screener.data.schwab.auth.httpx.Client", return_value=mock_client):
        with pytest.raises(SchwabAuthExpired):
            auth.get_access_token()


def test_token_file_permissions_0600_after_refresh(auth, token_path):
    """Persisted token file must be chmod 0600 after a refresh."""
    past = datetime.now(timezone.utc) - timedelta(seconds=5)
    _write_token_file(token_path, expires_at=past)

    fresh_payload = {
        "access_token": "new-access-token",
        "refresh_token": "new-refresh-token",
        "expires_in": 1800,
    }
    mock_client = MagicMock()
    mock_client.__enter__.return_value.post.return_value = _MockResponse(200, fresh_payload)

    with patch("screener.data.schwab.auth.httpx.Client", return_value=mock_client):
        auth.get_access_token()

    mode = stat.S_IMODE(os.stat(token_path).st_mode)
    assert mode == 0o600


def test_authorize_exchanges_code_for_token(auth, token_path):
    """Isolated coverage of authorize()'s token-exchange call shape, without
    driving a real browser, a real socket, or real TLS: stub out the
    loopback server capture to hand back a fixed code, patch the self-signed
    cert generation to dummy paths, and patch ssl.SSLContext so no real
    certificate loading/socket wrapping is attempted. Assert the token POST
    is shaped correctly and the response gets persisted."""
    fresh_payload = {
        "access_token": "authorized-access-token",
        "refresh_token": "authorized-refresh-token",
        "expires_in": 1800,
    }
    mock_client = MagicMock()
    mock_client.__enter__.return_value.post.return_value = _MockResponse(200, fresh_payload)

    mock_server = MagicMock()

    def _fake_handle_request():
        # Mimic what the real _CallbackHandler.do_GET would set on the
        # server instance once the redirect request arrives.
        mock_server.auth_code = "captured-auth-code"
        mock_server.auth_error = None

    mock_server.handle_request.side_effect = _fake_handle_request

    with patch("screener.data.schwab.auth.webbrowser.open") as mock_open, \
         patch("screener.data.schwab.auth.HTTPServer", return_value=mock_server), \
         patch("screener.data.schwab.auth.httpx.Client", return_value=mock_client), \
         patch("screener.data.schwab.auth._generate_self_signed_cert", return_value=("/tmp/fake-cert.pem", "/tmp/fake-key.pem")), \
         patch("screener.data.schwab.auth.ssl.SSLContext"):
        auth.authorize()

    mock_open.assert_called_once()
    called_url = mock_open.call_args[0][0]
    assert "client_id=test-app-key" in called_url
    assert "api.schwabapi.com/v1/oauth/authorize" in called_url

    post_kwargs = mock_client.__enter__.return_value.post.call_args
    assert post_kwargs is not None
    _, kwargs = post_kwargs
    assert kwargs["data"]["grant_type"] == "authorization_code"
    assert kwargs["data"]["code"] == "captured-auth-code"
    assert kwargs["auth"] == (APP_KEY, APP_SECRET)

    persisted = json.loads(token_path.read_text())
    assert persisted["access_token"] == "authorized-access-token"


def test_authorize_no_code_raises_expired(auth):
    """If the loopback redirect never yields a code (user cancels, error
    param instead), authorize() must raise SchwabAuthExpired rather than
    silently proceeding."""
    mock_server = MagicMock()
    mock_server.auth_code = None
    mock_server.auth_error = "access_denied"

    with patch("screener.data.schwab.auth.webbrowser.open"), \
         patch("screener.data.schwab.auth.HTTPServer", return_value=mock_server), \
         patch("screener.data.schwab.auth._generate_self_signed_cert", return_value=("/tmp/fake-cert.pem", "/tmp/fake-key.pem")), \
         patch("screener.data.schwab.auth.ssl.SSLContext"):
        with pytest.raises(SchwabAuthExpired):
            auth.authorize()


def test_generate_self_signed_cert_produces_loadable_cert():
    """_generate_self_signed_cert must return a (certfile, keyfile) tuple
    whose contents are a genuinely valid, loadable cert/key pair — proven by
    actually constructing an ssl.SSLContext and calling load_cert_chain on
    them, with no mocking of the crypto path. This does not need a real
    socket or browser; it exercises the cert-generation helper directly."""
    certfile, keyfile = _generate_self_signed_cert("127.0.0.1")
    try:
        assert isinstance(certfile, str)
        assert isinstance(keyfile, str)
        assert os.path.exists(certfile)
        assert os.path.exists(keyfile)

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile, keyfile)  # must not raise
    finally:
        # authorize() cleans these up itself after the loopback server
        # closes, but this test calls the helper directly (bypassing
        # authorize()), so it must clean up after itself here.
        os.remove(certfile)
        os.remove(keyfile)
        os.rmdir(os.path.dirname(certfile))


@pytest.mark.integration
def test_real_authorize_flow():
    """Live flow — opens a real browser and a real local socket, and hits
    the real Schwab OAuth endpoints. Skipped in CI with -m 'not integration'.
    Requires SCHWAB_APP_KEY/SCHWAB_APP_SECRET to be configured and a human
    present to complete the browser login."""
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
        token = auth.get_access_token()
        assert token
