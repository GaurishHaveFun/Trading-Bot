"""APScheduler-based cron scheduler for the stock screener."""
from __future__ import annotations

import asyncio
import signal
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from screener.config import load_rules_config
from screener.main import run_screener
from screener.utils.logging import configure_logging, get_logger

logger = get_logger(__name__)

_RULES_PATH = Path("config/rules.yaml")


async def _scheduled_run() -> None:
    """Job target for the cron trigger. Wraps `run_screener` to bake in
    `check_market_hours=True` — only scheduled runs are gated on the
    equity market being open today; manual `--once`/`--ticker` invocations
    call `run_screener()` directly with the default `check_market_hours=False`
    and are never gated.

    Defined as a real module-level `async def` (rather than passing
    `functools.partial(run_screener, check_market_hours=True)` straight to
    `add_job`) because APScheduler's `AsyncIOExecutor` decides how to invoke
    a job via `apscheduler.util.iscoroutinefunction_partial(job.func)`.
    Reading the installed apscheduler 3.11.2 source
    (`.venv/lib/python3.12/site-packages/apscheduler/util.py`), that helper
    DOES unwrap `functools.partial` before checking
    `asyncio.iscoroutinefunction`, so a `functools.partial`-wrapped
    `run_screener` would in fact be detected correctly on this installed
    version. A plain wrapper function is used anyway: it doesn't depend on
    that partial-unwrapping detail of the installed apscheduler version
    holding on every future upgrade, and it's unambiguous either way this
    executor's version-detection logic evolves.
    """
    await run_screener(check_market_hours=True)


def start() -> None:
    """Start the async cron scheduler. Blocks until SIGINT/SIGTERM."""
    asyncio.run(_run_scheduler())


async def _run_scheduler() -> None:
    rules_config = load_rules_config(_RULES_PATH)
    cron_expr = rules_config.schedule.on          # "0 16 * * 1-5"
    tz = rules_config.schedule.timezone           # "America/New_York"

    configure_logging()

    scheduler = AsyncIOScheduler()
    trigger = CronTrigger.from_crontab(cron_expr, timezone=tz)
    scheduler.add_job(_scheduled_run, trigger=trigger, id="screener")
    scheduler.start()

    logger.info("scheduler_started", cron=cron_expr, timezone=tz)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _handle_signal(sig):
        logger.info("shutdown_signal", signal=sig.name)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_signal, sig)

    await stop_event.wait()

    logger.info("scheduler_stopping")
    scheduler.shutdown(wait=False)
    logger.info("scheduler_stopped")
