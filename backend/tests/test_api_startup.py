"""Tests for the FastAPI startup lifespan hook in `screener.api.app` that
materializes `SCHWAB_TOKEN_JSON` (an env var, for Render's ephemeral
filesystem) to disk at `settings.schwab_token_path`. Split into its own file
(rather than `test_api.py`) because each test needs to enter `TestClient` as
a context manager to trigger lifespan startup, and construct its own
`Settings` per-test rather than sharing the module-scoped `client` in
`test_api.py`."""
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

import screener.api.app as api_app
from screener.api.app import app
from screener.config import Settings


def _settings(tmp_path, **overrides) -> Settings:
    defaults = dict(
        _env_file=None,
        schwab_token_path=str(tmp_path / "schwab_token.json"),
    )
    defaults.update(overrides)
    return Settings(**defaults)


def test_lifespan_writes_token_file_when_env_var_set(tmp_path, monkeypatch):
    token_json = '{"access_token": "abc123"}'
    monkeypatch.setenv("SCHWAB_TOKEN_JSON", token_json)
    settings = _settings(tmp_path)

    with patch("screener.api.app.get_settings", return_value=settings):
        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200

    token_path = tmp_path / "schwab_token.json"
    assert token_path.exists()
    assert token_path.read_text() == token_json


def test_lifespan_is_noop_when_env_var_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("SCHWAB_TOKEN_JSON", raising=False)
    settings = _settings(tmp_path)

    with patch("screener.api.app.get_settings", return_value=settings):
        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200

    token_path = tmp_path / "schwab_token.json"
    assert not token_path.exists()


def test_lifespan_builds_bar_provider_and_closes_cache_on_exit(tmp_path, monkeypatch):
    monkeypatch.delenv("SCHWAB_TOKEN_JSON", raising=False)
    settings = _settings(tmp_path)

    with patch("screener.api.app.get_settings", return_value=settings), \
         patch("screener.api.app._BAR_CACHE_PATH", tmp_path / "bars.db"):
        assert api_app._bar_provider is None
        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200
            assert api_app._bar_provider is not None
            assert api_app._bar_cache is not None

        # After lifespan shutdown, the cache is closed and globals reset.
        assert api_app._bar_provider is None
        assert api_app._bar_cache is None


def test_lifespan_is_noop_when_env_var_whitespace_only(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHWAB_TOKEN_JSON", "   ")
    settings = _settings(tmp_path)

    with patch("screener.api.app.get_settings", return_value=settings):
        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200

    token_path = tmp_path / "schwab_token.json"
    assert not token_path.exists()
