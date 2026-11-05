"""Push Yahoo `info` snapshots from this machine to the deployed API.

Run with:

    cd backend && uv run python scripts/push_yahoo_snapshot.py [--dry-run]

Needs PUSH_SECRET and PUSH_TARGET_URL in backend/.env (or the environment).
Yahoo blocks Render's IPs, so this residential-IP machine fetches the data
and the API stores/serves it. Exits non-zero if nothing was pushed.

Human-run CLI script: print() is fine here.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone

from screener.api.snapshot_pusher import (
    build_ticker_list,
    fetch_infos,
    is_market_open,
    push_infos,
)
from screener.config import get_settings, load_watchlist


async def run(dry_run: bool, if_market_open: bool = False) -> int:
    settings = get_settings()
    if if_market_open and not is_market_open(datetime.now(timezone.utc)):
        print("Market closed (Mon-Fri 9:30-16:00 ET); skipping.")
        return 0
    if not dry_run and (not settings.push_secret or not settings.push_target_url):
        print("Missing PUSH_SECRET and/or PUSH_TARGET_URL (set in backend/.env).")
        return 1

    watchlist = load_watchlist(settings.watchlist_path)
    symbols = await build_ticker_list(watchlist)
    print(f"Fetching Yahoo info for {len(symbols)} symbols...")
    infos = await fetch_infos(symbols)
    print(f"Got info for {len(infos)}/{len(symbols)} symbols.")

    if dry_run:
        print("Dry run, not pushing. Symbols: " + ", ".join(sorted(infos)))
        return 0
    if not infos:
        print("Nothing to push.")
        return 1

    pushed = await push_infos(infos, settings.push_target_url, settings.push_secret)
    print(f"Pushed {pushed} snapshots to {settings.push_target_url}.")
    return 0 if pushed else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="fetch but do not push")
    parser.add_argument(
        "--if-market-open", action="store_true",
        help="exit 0 silently unless it is Mon-Fri 9:30-16:00 America/New_York (holidays not handled)",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.dry_run, args.if_market_open)))


if __name__ == "__main__":
    main()
