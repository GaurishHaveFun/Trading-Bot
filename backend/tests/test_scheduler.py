"""Tests for the scheduler module."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from screener.scheduler import _run_scheduler


def test_scheduler_module_importable():
    """Scheduler module must import without error."""
    import screener.scheduler
    assert hasattr(screener.scheduler, "start")
    assert hasattr(screener.scheduler, "_run_scheduler")


def test_start_is_callable():
    from screener.scheduler import start
    assert callable(start)
