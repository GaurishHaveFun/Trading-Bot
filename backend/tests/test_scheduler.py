"""Tests for the scheduler module."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from screener.scheduler import _run_scheduler, _scheduled_run


def test_scheduler_module_importable():
    """Scheduler module must import without error."""
    import screener.scheduler
    assert hasattr(screener.scheduler, "start")
    assert hasattr(screener.scheduler, "_run_scheduler")


def test_start_is_callable():
    from screener.scheduler import start
    assert callable(start)


async def test_scheduled_run_calls_run_screener_with_check_market_hours_true():
    """The job target registered with APScheduler must call `run_screener`
    with `check_market_hours=True` — this is what actually gates scheduled
    (cron) runs on the equity market being open, per the task spec (manual
    `--once`/`--ticker` invocations call `run_screener()` directly and are
    never gated)."""
    with patch("screener.scheduler.run_screener", new_callable=AsyncMock) as mock_run_screener:
        await _scheduled_run()

    mock_run_screener.assert_called_once_with(check_market_hours=True)


async def test_run_scheduler_registers_scheduled_run_as_the_job():
    """`_run_scheduler` must register `_scheduled_run` (not the bare
    `run_screener`) as the cron job — this is what bakes in
    `check_market_hours=True` for scheduled runs only. Verified end-to-end:
    whatever callable ends up passed to `add_job`, when invoked/awaited,
    results in `run_screener` being called with `check_market_hours=True`."""
    mock_scheduler_instance = MagicMock()
    fake_rules_config = MagicMock()
    fake_rules_config.schedule.on = "0 16 * * 1-5"
    fake_rules_config.schedule.timezone = "America/New_York"

    fake_stop_event = MagicMock()
    fake_stop_event.wait = AsyncMock(return_value=None)

    with patch("screener.scheduler.load_rules_config", return_value=fake_rules_config), \
         patch("screener.scheduler.configure_logging"), \
         patch("screener.scheduler.AsyncIOScheduler", return_value=mock_scheduler_instance), \
         patch("screener.scheduler.CronTrigger"), \
         patch("screener.scheduler.asyncio.Event", return_value=fake_stop_event), \
         patch("screener.scheduler.run_screener", new_callable=AsyncMock) as mock_run_screener:
        await _run_scheduler()

        assert mock_scheduler_instance.add_job.call_count == 1
        args, kwargs = mock_scheduler_instance.add_job.call_args
        job_func = args[0] if args else kwargs["func"]
        assert job_func is _scheduled_run

        # Invoke whatever was registered (still inside the patch context, so
        # the module-level `run_screener` name it looks up is still mocked)
        # and confirm it drives run_screener with check_market_hours=True.
        await job_func()
        mock_run_screener.assert_called_once_with(check_market_hours=True)
