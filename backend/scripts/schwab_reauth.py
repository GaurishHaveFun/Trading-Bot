"""One-command local weekly Schwab re-auth + Render sync.

Run with:

    cd backend && uv run python scripts/schwab_reauth.py

Steps:
  1. Validate `RENDER_API_KEY` / `RENDER_SERVICE_ID` are set (fail fast,
     before opening a browser).
  2. Run the existing interactive Schwab OAuth flow
     (`screener.main.run_auth_schwab`) — reused, not reimplemented.
  3. Push the resulting token file's contents to Render as the
     `SCHWAB_TOKEN_JSON` env var.
  4. Trigger a Render redeploy so the running service picks it up.

Human-run CLI script: print() throughout, no structlog.
"""
from __future__ import annotations

import sys

import httpx

from screener.config import get_settings

RENDER_API_BASE = "https://api.render.com/v1"


def main() -> None:
    settings = get_settings()

    if not settings.render_api_key or not settings.render_service_id:
        print(
            "Missing RENDER_API_KEY and/or RENDER_SERVICE_ID.\n"
            "Set both in backend/.env before running this script:\n"
            "  - RENDER_API_KEY: generate from your Render account's "
            "API Keys page (dashboard -> account settings -> API Keys).\n"
            "  - RENDER_SERVICE_ID: copy from the trading-bot-api service's "
            "dashboard URL/settings page (format: srv-xxxxx)."
        )
        sys.exit(1)

    print("Step 1/3: opening browser for Schwab login...")
    from screener.main import run_auth_schwab

    run_auth_schwab()

    token_json = open(settings.schwab_token_path, "r").read()

    headers = {
        "Authorization": f"Bearer {settings.render_api_key}",
        "Content-Type": "application/json",
    }

    print("Step 2/3: pushing token to Render...")
    env_var_url = (
        f"{RENDER_API_BASE}/services/{settings.render_service_id}"
        "/env-vars/SCHWAB_TOKEN_JSON"
    )
    resp = httpx.put(env_var_url, headers=headers, json={"value": token_json})
    if not resp.is_success:
        print(f"Failed to push token to Render: {resp.status_code} {resp.text}")
        sys.exit(1)

    print("Step 3/3: triggering redeploy...")
    deploy_url = f"{RENDER_API_BASE}/services/{settings.render_service_id}/deploys"
    resp = httpx.post(deploy_url, headers=headers, json={})
    if not resp.is_success:
        print(f"Failed to trigger Render redeploy: {resp.status_code} {resp.text}")
        sys.exit(1)

    print("Done — Render will be live with the new token in ~1-2 minutes.")


if __name__ == "__main__":
    main()
