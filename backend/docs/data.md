# data — Bar Cache

## Purpose

`screener.data.cache.BarCache` is a thin SQLite persistence layer for OHLCV bar data. It sits between the data provider (e.g. yfinance) and the indicator/rule engine, ensuring that every network fetch is stored locally so subsequent runs — and re-runs during development — never redundantly hit the network.

The cache is keyed on `(symbol, timestamp, interval)`, making it safe to store bars from multiple symbols and multiple bar intervals (daily `1d`, hourly `1h`, etc.) in a single database file.

---

## DB Schema and File Location

Default path: `.cache/bars.db` (relative to the working directory, typically `backend/`).

The path is configurable by passing `db_path` to `BarCache.__init__`. The parent directory is created automatically if it does not exist.

```sql
CREATE TABLE IF NOT EXISTS bars (
    symbol    TEXT    NOT NULL,
    timestamp TEXT    NOT NULL,   -- ISO-8601, always UTC (e.g. "2024-01-05T21:00:00+00:00")
    interval  TEXT    NOT NULL,   -- e.g. "1d", "1h"
    open      REAL    NOT NULL,
    high      REAL    NOT NULL,
    low       REAL    NOT NULL,
    close     REAL    NOT NULL,
    volume    INTEGER NOT NULL,
    PRIMARY KEY (symbol, timestamp, interval)
)
```

Timestamps are stored as UTC ISO-8601 strings. The `Bar.timestamp` validator in `models.py` guarantees all timestamps round-trip as UTC-aware `datetime` objects.

---

## API

### `BarCache(db_path: str | Path = ".cache/bars.db")`

Opens (or creates) the SQLite database at `db_path`. The `bars` table is created on first use. The connection is kept open for the lifetime of the object; call `close()` when done.

---

### `get(symbol, start, end, interval="1d") → list[Bar]`

Returns all cached bars for `symbol` and `interval` where the bar's **date** falls within `[start, end]` inclusive.

| Parameter  | Type       | Description                                        |
|------------|------------|----------------------------------------------------|
| `symbol`   | `str`      | Ticker symbol, e.g. `"AAPL"`                       |
| `start`    | `datetime` | Start of date range (any time-of-day; only the date part is compared) |
| `end`      | `datetime` | End of date range inclusive (any time-of-day; only the date part is compared) |
| `interval` | `str`      | Bar interval, default `"1d"`                       |

The range comparison uses SQLite's `DATE(timestamp)` function so that daily bars stored at market-close time (e.g. `21:00 UTC` for 4pm ET) are correctly matched against midnight-normalized date boundaries.

Returns an empty list if no bars are found.

---

### `put(symbol, bars, interval="1d") → None`

Persists a list of `Bar` objects to the cache. Each bar's timestamp is converted to UTC before storage.

| Parameter  | Type        | Description                             |
|------------|-------------|-----------------------------------------|
| `symbol`   | `str`       | Ticker symbol                           |
| `bars`     | `list[Bar]` | Bars to persist                         |
| `interval` | `str`       | Bar interval, default `"1d"`            |

---

### `close() → None`

Closes the underlying SQLite connection. Should be called when the cache is no longer needed (e.g. at the end of a run or in a `finally` block).

---

## Duplicate Handling

`put()` uses `INSERT OR IGNORE` — if a row with the same `(symbol, timestamp, interval)` primary key already exists, the insert is silently skipped. This means:

- Re-running the screener never raises an error if bars are already cached.
- Partial overlaps (e.g. fetching a range that includes already-cached days) are handled correctly without duplicating data.
- Values already in the cache are **not** updated; if a data correction is needed, the `.cache/bars.db` file must be deleted or patched manually.

---

## Composition with the Data Provider

`YFinanceProvider` uses `BarCache` as a mandatory first-look before any network call, satisfying the non-negotiable: "Cache every yfinance fetch to SQLite."

Thread safety: `sqlite3.connect(..., check_same_thread=False)` allows the connection to be shared across threads, which is safe for the single-writer / multiple-reader pattern used by the async provider (writes are serialized at the Python level through `asyncio.Semaphore(10)` on the provider side).

---

## DataProvider ABC

`screener.data.base.DataProvider` is the abstract base class for all data providers.

```python
class DataProvider(ABC):
    @abstractmethod
    async def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: str = "1d",
    ) -> list:
        """Fetch OHLCV bars for symbol between start and end (UTC datetimes)."""
```

All concrete providers must implement `get_bars` as a coroutine. This interface allows future providers (Alpaca, Finnhub, etc.) to be swapped in without changing callers.

---

## YFinanceProvider

`screener.data.yfinance_provider.YFinanceProvider` implements `DataProvider` with cache-first logic and `asyncio.to_thread` wrapping for the blocking yfinance library.

### Construction

```python
provider = YFinanceProvider(cache=BarCache())
```

`BarCache` is a mandatory dependency — the provider will never make a network call without also writing the result to cache.

### Cache-First Logic

`get_bars` follows this decision tree on every call:

1. Query `BarCache.get(symbol, start, end, interval)`.
2. If the cache is **empty** — fetch the entire range from yfinance, write to cache, return bars.
3. If the cache has **some data** — compare `start.date()` with `cached_start.date()` and `end.date()` with `cached_end.date()`:
   - If `start.date() < cached_start.date()` → fetch the missing head and write to cache.
   - If `end.date() > cached_end.date()` → fetch the missing tail and write to cache.
   - Re-query the cache to return the fully merged result.

Date comparison (`.date()`) is used deliberately: daily bars are stored at market-close time (e.g. `21:00 UTC` for 4 pm ET), so a naive datetime comparison against midnight-normalized `start`/`end` boundaries would falsely detect gaps on the same calendar day.

### asyncio.to_thread Wrapping

yfinance's `download()` is a blocking network call. All downloads are dispatched via `asyncio.to_thread(self._download, ...)`, keeping the event loop unblocked. The internal split between `_fetch` (async, adds the polite delay) and `_download` (sync, the actual yfinance call) makes the blocking portion easy to mock in tests.

### Fetch Delay

A `0.2`-second `asyncio.sleep` is inserted before every live yfinance fetch. This acts as a polite rate-limiter when fetching multiple tickers concurrently and avoids triggering Yahoo Finance's informal rate limits.

### UTC Timestamp Handling

yfinance returns timestamps whose timezone-awareness varies by version:

- Newer versions return tz-aware `Timestamp` objects (UTC or exchange-local).
- Older versions return tz-naive `Timestamp` objects.

`_df_to_bars` handles both cases:

```python
if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
    dt = ts.to_pydatetime().astimezone(timezone.utc)
else:
    dt = ts.to_pydatetime().replace(tzinfo=timezone.utc)
```

All `Bar.timestamp` values are guaranteed UTC-aware by the time they leave `YFinanceProvider`. The `Bar.must_be_utc` field validator in `models.py` enforces this as a second line of defense.

### Column Name Note

`yf.download(..., multi_level_column=False)` is required to flatten multi-ticker DataFrames into single-level columns (`Open`, `High`, `Low`, `Close`, `Volume`). Without this flag, yfinance >= 0.2.x returns a MultiIndex DataFrame that requires an extra level of access.
