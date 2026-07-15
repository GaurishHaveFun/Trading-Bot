"""Schwab Trader API OAuth authentication (Milestone 1: config + auth foundation only).

This module implements Schwab's 3-legged OAuth flow:
  1. `authorize()` — a one-time, interactive, sync CLI flow (invoked via
     `--auth-schwab`, never from an async request path): opens the user's
     browser at Schwab's authorize URL, stands up a temporary local loopback
     server to capture the redirect's `code` query param, exchanges that code
     for an access/refresh token pair, and persists them to `token_path`.
  2. `get_access_token()` — returns a valid access token for use by later
     milestones' API clients, transparently refreshing it when it is close to
     expiry, using the (up to 7-day-lived) refresh token.

Schwab access tokens last 30 minutes; refresh tokens last 7 days. Once the
refresh token itself expires, Schwab requires a fresh interactive browser
re-authorization — there is no programmatic way around this restriction, so
`get_access_token()` surfaces that condition as `SchwabAuthExpired` for the
caller (and, in later milestones, fallback logic) to handle.

HTTPS loopback callback:
Schwab requires the registered callback URL to use `https://`. To satisfy
that, `authorize()` generates a throwaway self-signed X.509 certificate (via
the `cryptography` package) at runtime and terminates TLS directly in the
loopback server, purely so the local redirect target is reachable over
HTTPS. Because the cert is self-signed and not from a trusted CA, the
browser will show a one-time "your connection is not private / not secure"
warning on the redirect — the user must click through it to proceed. This is
expected/normal for a local-loopback OAuth dev flow, not a bug.
"""
from __future__ import annotations

import ipaddress
import json
import os
import ssl
import tempfile
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from screener.utils.logging import get_logger

logger = get_logger(__name__)

_AUTHORIZE_URL = "https://api.schwabapi.com/v1/oauth/authorize"
_TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
_EXPIRY_BUFFER_SECONDS = 60
_HTTP_TIMEOUT_SECONDS = 30.0


class SchwabAuthExpired(Exception):
    """Raised when there is no valid way to obtain a Schwab access token
    without interactive re-authorization: either no token file exists yet
    (never authorized via `--auth-schwab`), or the stored refresh token was
    rejected by Schwab's token endpoint (the 7-day refresh token has died).
    Callers (including later fallback-provider logic) should catch this one
    exception type and prompt the user to re-run `--auth-schwab`.
    """


def _generate_self_signed_cert(host: str) -> tuple[str, str]:
    """Generate a throwaway self-signed X.509 certificate + private key,
    valid for the given loopback `host`, and write them to temporary PEM
    files (since `ssl.SSLContext.load_cert_chain` requires file paths).

    The cert's SANs always include both "127.0.0.1" and "localhost" (for
    robustness across however the callback URL happens to be configured),
    plus the actual parsed `host` if it differs from those.

    Returns the (certfile_path, keyfile_path) tuple. Callers are responsible
    for deleting these temporary files once the server that loaded them has
    closed.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, host)]
    )

    san_hosts = {"127.0.0.1", "localhost", host}
    san_entries = [x509.DNSName(h) for h in san_hosts]
    try:
        san_entries.append(x509.IPAddress(ipaddress.ip_address(host)))
    except ValueError:
        pass  # host isn't a literal IP address (e.g. "localhost") — DNSName covers it

    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .sign(key, hashes.SHA256())
    )

    tmp_dir = tempfile.mkdtemp(prefix="schwab_selfsigned_")
    certfile = os.path.join(tmp_dir, "cert.pem")
    keyfile = os.path.join(tmp_dir, "key.pem")

    with open(certfile, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(keyfile, "wb") as f:
        f.write(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

    return certfile, keyfile


class _CallbackHandler(BaseHTTPRequestHandler):
    """Captures the `code` (or `error`) query param from Schwab's OAuth
    redirect, then responds with a short human-readable HTML page. The
    HTTPServer instance stores the captured values as `auth_code`/`auth_error`
    attributes so `authorize()` can read them after `handle_request()`
    returns."""

    def do_GET(self) -> None:  # noqa: N802 (stdlib method name)
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        error = params.get("error", [None])[0]
        self.server.auth_code = code  # type: ignore[attr-defined]
        self.server.auth_error = error  # type: ignore[attr-defined]

        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        if code:
            body = b"<html><body>Schwab authorization complete. You may close this window.</body></html>"
        else:
            body = b"<html><body>Schwab authorization failed. You may close this window.</body></html>"
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Suppress BaseHTTPRequestHandler's default stderr access logging —
        this codebase logs via structlog only, never bare stdout/stderr
        writes from library code."""
        return


class SchwabAuth:
    """Handles the Schwab 3-legged OAuth flow: one-time interactive
    authorization plus ongoing access-token refresh."""

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
        """One-time interactive OAuth flow: open the browser at Schwab's
        authorize URL, capture the redirect's `code` via a temporary local
        loopback server, exchange it for tokens, and persist them.

        This is a deliberate synchronous, blocking, interactive CLI flow —
        not called from any async request path — so a sync `httpx.Client`
        and a blocking `HTTPServer.handle_request()` call are correct here.

        See the module docstring for why the browser shows a one-time
        self-signed-certificate warning during this flow.
        """
        auth_url = f"{_AUTHORIZE_URL}?{urlencode({'client_id': self._app_key, 'redirect_uri': self._callback_url})}"

        parsed_cb = urlparse(self._callback_url)
        host = parsed_cb.hostname or "127.0.0.1"
        port = parsed_cb.port or (443 if parsed_cb.scheme == "https" else 80)

        server = HTTPServer((host, port), _CallbackHandler)
        server.auth_code = None  # type: ignore[attr-defined]
        server.auth_error = None  # type: ignore[attr-defined]

        certfile, keyfile = _generate_self_signed_cert(host)
        logger.info("schwab_authorize_selfsigned_cert_generated", callback_host=host)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile, keyfile)
        server.socket = context.wrap_socket(server.socket, server_side=True)

        logger.info("schwab_authorize_start", callback_host=host, callback_port=port)
        webbrowser.open(auth_url)

        try:
            server.handle_request()  # blocks until the single redirect request arrives
        finally:
            server.server_close()
            for path in (certfile, keyfile):
                try:
                    os.remove(path)
                except OSError:
                    pass
            try:
                os.rmdir(os.path.dirname(certfile))
            except OSError:
                pass

        code = server.auth_code  # type: ignore[attr-defined]
        error = server.auth_error  # type: ignore[attr-defined]
        if not code:
            logger.warning("schwab_authorize_no_code", error=error)
            raise SchwabAuthExpired(
                f"Schwab authorization did not return a code (error={error!r}); "
                "run --auth-schwab again."
            )

        logger.info("schwab_authorize_code_received")

        with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            response = client.post(
                _TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self._callback_url,
                },
                auth=(self._app_key, self._app_secret),
            )
        response.raise_for_status()

        self._persist_token(response.json())
        logger.info("schwab_authorize_complete", token_path=str(self._token_path))

    def get_access_token(self) -> str:
        """Return a valid Schwab access token, refreshing it via the stored
        refresh token if it is within `_EXPIRY_BUFFER_SECONDS` of expiry (or
        already expired). Raises `SchwabAuthExpired` if no token file exists
        yet, or if the refresh attempt is rejected by Schwab (refresh token
        dead after 7 days) — in either case the caller must re-run
        `--auth-schwab`.
        """
        if not self._token_path.exists():
            logger.warning("schwab_token_missing", token_path=str(self._token_path))
            raise SchwabAuthExpired(
                f"No Schwab token file at {self._token_path} — run --auth-schwab to authorize."
            )

        token_data = json.loads(self._token_path.read_text())
        expires_at = datetime.fromisoformat(token_data["expires_at"])
        now = datetime.now(timezone.utc)

        if (expires_at - now).total_seconds() > _EXPIRY_BUFFER_SECONDS:
            return token_data["access_token"]

        logger.info("schwab_token_refresh_start")
        with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            response = client.post(
                _TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": token_data["refresh_token"],
                },
                auth=(self._app_key, self._app_secret),
            )

        if response.status_code != 200:
            logger.warning("schwab_token_refresh_rejected", status_code=response.status_code)
            raise SchwabAuthExpired(
                "Schwab refresh token was rejected (likely expired after 7 days) — "
                "run --auth-schwab to re-authorize."
            )

        new_token_data = self._persist_token(response.json())
        logger.info("schwab_token_refresh_complete")
        return new_token_data["access_token"]

    def _persist_token(self, payload: dict) -> dict:
        """Write the token payload (access_token/refresh_token/expires_in)
        to `token_path` as JSON with issued_at/expires_at UTC timestamps,
        creating parent dirs as needed and locking permissions to 0600.
        Never logs token contents. Returns the persisted dict."""
        issued_at = datetime.now(timezone.utc)
        expires_in = payload.get("expires_in", 1800)
        expires_at = issued_at + timedelta(seconds=expires_in)

        token_data = {
            "access_token": payload["access_token"],
            "refresh_token": payload["refresh_token"],
            "issued_at": issued_at.isoformat(),
            "expires_at": expires_at.isoformat(),
        }

        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        self._token_path.write_text(json.dumps(token_data))
        os.chmod(self._token_path, 0o600)

        return token_data
