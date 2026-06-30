"""Writes ScreenerRun results to a timestamped JSON file."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from screener.models import ScreenerRun
from screener.utils.logging import get_logger

logger = get_logger(__name__)

_OUTPUT_DIR = Path("output/runs")


def write_run(run: ScreenerRun, output_dir: Path = _OUTPUT_DIR) -> Path:
    """Write a ScreenerRun to output/runs/run_<UTC_ISO>.json. Returns the path written."""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = run.run_timestamp.strftime("%Y%m%dT%H%M%SZ")
    path = output_dir / f"run_{ts}.json"

    payload = _serialise(run)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)

    logger.info("run_written", path=str(path), signals=len(run.signals))
    return path


def _serialise(run: ScreenerRun) -> dict:
    """Convert ScreenerRun to the locked JSON schema dict."""
    return {
        "run_timestamp": _fmt(run.run_timestamp),
        "universe": run.universe,
        "alert_threshold": run.alert_threshold,
        "signals": [_serialise_signal(s) for s in run.signals],
    }


def _serialise_signal(signal) -> dict:
    return {
        "ticker": signal.ticker,
        "timestamp": _fmt(signal.timestamp),
        "score": round(signal.score, 4),
        "rules_passed": signal.rules_passed,
        "rules_total": signal.rules_total,
        "snapshot": signal.snapshot,
        "rule_results": [
            {
                "rule_name": r.rule_name,
                "passed": r.passed,
                "weight": r.weight,
                "detail": r.detail,
            }
            for r in signal.rule_results
        ],
    }


def _fmt(dt: datetime) -> str:
    """Format datetime as UTC ISO 8601 with Z suffix."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
