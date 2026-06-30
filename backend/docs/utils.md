# utils — Logging & Scheduler

## Overview

The `screener.utils` package provides two concerns:

1. **`screener.utils.logging`** — structured JSON logging configuration via `structlog`.
2. **`screener.scheduler`** — APScheduler-based cron loop that drives the screener on a schedule.

---

## `screener.utils.logging`

### `configure_logging(level: str = "INFO") -> None`

Configures `structlog` to emit machine-readable JSON to stdout. Must be called once at application start (done automatically by `main.py` before any pipeline work begins, and by the scheduler before it starts).

Processor chain applied to every log record:

| Processor | Purpose |
|---|---|
| `merge_contextvars` | Merges any thread/async context variables into the event dict |
| `add_log_level` | Injects `"level"` key (e.g. `"info"`, `"warning"`) |
| `TimeStamper(fmt="iso", utc=True)` | Injects `"timestamp"` in ISO 8601 UTC format |
| `JSONRenderer` | Serialises the event dict to a single-line JSON string |

The underlying Python `logging` module is also configured at the same level so that third-party libraries that use `logging` (e.g. APScheduler) produce plain `%(message)s`-formatted lines compatible with the JSON stream.

### `get_logger(name: str) -> structlog.BoundLogger`

Returns a named structlog bound logger. Usage throughout the codebase:

```python
from screener.utils.logging import get_logger
logger = get_logger(__name__)
logger.info("event_name", key="value")
```

### Why JSON logs

- **Machine-readable**: log aggregators (Datadog, CloudWatch, Splunk) can parse fields without regex.
- **No `print()`**: `print()` is banned in library code (CLAUDE.md rule 6). All output goes through `structlog`.
- **UTC timestamps**: every record carries a UTC ISO 8601 timestamp, consistent with the non-negotiable rule that all times are UTC internally.

---

## `screener.scheduler`

File: `src/screener/scheduler.py`

### `start() -> None`

Public entrypoint called from `main.py` when no `--once` or `--ticker` flag is given. Calls `asyncio.run(_run_scheduler())` and blocks until SIGINT or SIGTERM is received.

### `_run_scheduler() -> None` (async)

Internal coroutine that:

1. Loads `config/rules.yaml` to read the cron expression (`schedule.on`) and timezone (`schedule.timezone`).
2. Calls `configure_logging()` to set up structured JSON output.
3. Creates an `AsyncIOScheduler` (APScheduler's asyncio-native backend) and registers `run_screener` on a `CronTrigger`.
4. Registers OS signal handlers for `SIGINT` and `SIGTERM` via `loop.add_signal_handler` (the asyncio-safe API — not `signal.signal`).
5. Blocks on `stop_event.wait()`.
6. On signal: sets the stop event → shuts the scheduler down with `wait=False` (non-blocking to avoid deadlock inside the event loop).

### `CronTrigger.from_crontab`

APScheduler's `CronTrigger.from_crontab(expr, timezone=tz)` parses a standard 5-field cron expression. The default schedule from `rules.yaml` is `"0 16 * * 1-5"` (4:00 PM US/Eastern on weekdays), which fires just after NYSE close.

### Signal handling

`loop.add_signal_handler` is used instead of `signal.signal` because the screener runs inside an asyncio event loop. The former queues the callback as a thread-safe scheduled callback; the latter is not safe to call from within a running loop on some platforms.

```
SIGINT / SIGTERM
      │
      ▼
 _handle_signal(sig)
      │  sets stop_event
      ▼
 stop_event.wait() returns
      │
      ▼
 scheduler.shutdown(wait=False)
```

### Graceful shutdown guarantee

`scheduler.shutdown(wait=False)` tells APScheduler to stop accepting new jobs immediately without waiting for currently-running jobs to finish. Because `run_screener` is itself an async coroutine (not a thread), APScheduler will not interrupt a mid-run screener; the shutdown simply prevents the next scheduled fire.
