# Indicators Library (`src/screener/indicators/`)

## Purpose

The indicators library provides a thin, consistent interface between raw OHLCV bar data and the rule engine. Every function accepts a `pd.DataFrame` (columns: `timestamp`, `open`, `high`, `low`, `close`, `volume`) and returns a single scalar value representing the **latest** computed indicator for that series.

This design is intentional: the rule engine (`screener.rules`) needs one number per ticker per indicator to evaluate boolean expressions like `rsi(14) < 35`. Returning a full Series would force the rule engine to do its own slicing, which adds complexity and risk of off-by-one errors.

## Why Latest-Scalar (Not Full Series)

- The rule engine evaluates each rule against the most recent bar for a given ticker.
- Returning a scalar keeps rule expressions simple and readable (e.g., `close > sma(200)`).
- The 250-bar minimum enforced upstream (see `data/`) guarantees that all indicators have enough history to produce a valid (non-NaN) result when called with their standard periods.

## NaN Guarantee

All functions assume the caller has already enforced the 250-bar minimum (the project non-negotiable). With 250 bars:

| Indicator | Period | Warm-up bars needed |
|-----------|--------|---------------------|
| SMA       | 200    | 200                 |
| SMA       | 50     | 50                  |
| EMA       | 20     | 20                  |
| RSI       | 14     | 15 (needs 1 diff)   |
| ATR       | 14     | 14                  |
| SMA Volume| 20     | 20                  |

250 bars covers all cases. If a caller passes fewer rows, the return value may be `NaN` — that would be a caller bug, not a library bug.

## Functions

### `sma(df, period) -> float`

Simple Moving Average of the `close` column over the last `period` bars.

Wraps: `pandas_ta.sma(close, length=period)`

**Parameter:** `period` — integer lookback window (e.g., 50, 200).

**Used in rules:** `above_long_trend` (SMA 200), `golden_cross_state` (SMA 50 and SMA 200).

---

### `ema(df, period) -> float`

Exponential Moving Average of the `close` column over `period` bars. Uses the standard exponential decay formula (equivalent to `pandas.Series.ewm(span=period, adjust=False)`).

Wraps: `pandas_ta.ema(close, length=period)`

**Parameter:** `period` — integer span (e.g., 20).

---

### `rsi(df, period) -> float`

Relative Strength Index over `period` bars. Returns a value in [0, 100]. Values below 30 indicate oversold conditions; values above 70 indicate overbought.

Wraps: `pandas_ta.rsi(close, length=period)`

**Parameter:** `period` — integer lookback window (standard: 14).

**Used in rules:** `oversold_rsi` (RSI 14 < 35).

---

### `atr(df, period) -> float`

Average True Range over `period` bars. Uses `high`, `low`, and `close`. ATR measures volatility in price units.

Wraps: `pandas_ta.atr(high, low, close, length=period)`

**Parameter:** `period` — integer lookback window (standard: 14).

**Used in rules:** `reasonable_volatility` (ATR 14 / close < 0.05).

---

### `sma_volume(df, period) -> float`

Simple Moving Average of the `volume` column over the last `period` bars. Volume is cast to `float` before computing to avoid integer overflow on large volume values.

Wraps: `pandas_ta.sma(volume, length=period)`

**Parameter:** `period` — integer lookback window (e.g., 20).

**Used in rules:** `volume_spike` (volume > SMA volume 20 * 1.5).

---

### `latest_close(df) -> float`

Returns the single most recent close price. No computation — just `df["close"].iloc[-1]` cast to `float`.

---

### `latest_volume(df) -> int`

Returns the single most recent volume. Cast to `int` (volumes are always whole numbers).

## Internal Helpers

- `_close(df)` — extracts `df["close"]` as `float64` Series; used by all close-based indicators.
- `_volume(df)` — extracts `df["volume"]` as `float64` Series; used by `sma_volume`.

## Dependencies

- `pandas` — DataFrame manipulation.
- `pandas-ta` — technical indicator computation. **Do not swap this library without consulting the project spec** (CLAUDE.md specifies pandas-ta explicitly and mandates stopping if it fails).

## Module Exports (`__init__.py`)

```python
from screener.indicators.library import (
    sma, ema, rsi, atr, sma_volume, latest_close, latest_volume
)
```

All seven functions are re-exported from the package root so the rule engine can import cleanly with `from screener.indicators import sma, rsi, ...`.
