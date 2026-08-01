# Rules Module

The `screener/rules/` subpackage implements a safe, config-driven rule evaluation engine. It reads rule conditions from `config/rules.yaml`, evaluates each expression against a ticker's bar data via `asteval`, and returns a weighted score.

## Files

| File | Role |
|------|------|
| `engine.py` | `RuleEngine` class — evaluates rules, computes score |
| `functions.py` | Builds the asteval symbol table (indicator closures + scalars) |
| `__init__.py` | Public re-exports: `RuleEngine`, `build_symbol_table` |

---

## `RuleEngine.evaluate()` Flow

```
evaluate(
    symbol: str,
    bars: list[Bar],
    meta: dict | None = None,
    watchlist: set[str] | None = None,
) -> list[RuleResult]
```

1. Convert `bars` to a sorted pandas DataFrame via `_bars_to_df()`.
2. Extract `close` (latest close scalar) and `volume` (latest volume scalar) via `latest_close()` / `latest_volume()` from the indicators library.
3. Derive `in_watchlist` (`symbol in watchlist` if `watchlist` is given, else `False`), then call `build_symbol_table(df, close, volume, meta=meta, in_watchlist=in_watchlist)` to build a fresh symbol dict. `meta` is per-symbol quote metadata (price_to_book, change_pct, ...) from the universe provider's `get_quotes()`; `watchlist` is the set of big-tech/chip symbols.
4. For each `RuleConfig` in `self._rules`, call `_eval_rule()`:
   - Instantiate a new `asteval.Interpreter` with a **copy** of the symbol table (prevents state bleed across rules).
   - Run `aeval(rule.condition)` to evaluate the expression string.
   - If `aeval.error` is non-empty, log a warning and return `passed=False` with an error detail dict.
   - If the outcome is `None` (e.g. undefined variable that asteval silently returns None for), return `passed=False`.
   - Otherwise, cast the result to `bool` and call `_extract_detail()` to capture indicator values used in the condition.
5. Return the list of `RuleResult` objects.

---

## Why asteval Instead of eval()

`eval()` is banned throughout this codebase (non-negotiable per spec). An adversarial or misconfigured `rules.yaml` could inject arbitrary Python — `eval("__import__('os').system('rm -rf /')")` would execute unimpeded.

`asteval.Interpreter` parses the expression into an AST and walks it with a restricted evaluator that:
- Disallows imports, attribute access, and exec.
- Raises `NameNotDefined` for unknown symbols rather than falling through to builtins.
- Returns errors in `aeval.error` rather than raising exceptions, giving the engine a clean recovery path.

The engine never calls `eval()`, `exec()`, or `compile()`.

---

## `build_symbol_table()` — What's in the Symbol Table

```python
build_symbol_table(
    df: pd.DataFrame,
    close: float,
    volume: float,
    meta: dict | None = None,
    in_watchlist: bool = False,
) -> dict
```

`meta` is per-symbol quote metadata from the universe provider's `get_quotes()` (e.g. `price_to_book`, `change_pct`, `industry`). Missing values fall back to safe defaults (`price_to_book` → `inf`, `change_pct` → `0.0`, `industry` → `""`) so rule conditions never blow up on absent data. `in_watchlist` is passed in directly by the caller, derived from membership in the watchlist set of big-tech/chip symbols. `is_chip` is derived internally: `True` if the substring `"semiconductor"` appears (case-insensitively) in `industry`.

| Key | Type | Description |
|-----|------|-------------|
| `sma` | callable `(period: int) -> float` | Closure over `df`, calls `indicators.library.sma(df, period)` |
| `ema` | callable `(period: int) -> float` | Closure over `df`, calls `indicators.library.ema(df, period)` |
| `rsi` | callable `(period: int) -> float` | Closure over `df`, calls `indicators.library.rsi(df, period)` |
| `atr` | callable `(period: int) -> float` | Closure over `df`, calls `indicators.library.atr(df, period)` |
| `sma_volume` | callable `(period: int) -> float` | Closure over `df`, calls `indicators.library.sma_volume(df, period)` |
| `low_52w` | callable `(period: int = 252) -> float` | Closure over `df`, calls `indicators.library.low_52w(df, period)` |
| `high_52w` | callable `(period: int = 252) -> float` | Closure over `df`, calls `indicators.library.high_52w(df, period)` |
| `macd_line` | callable `(fast: int = 12, slow: int = 26, signal: int = 9) -> float` | Closure over `df`, calls `indicators.library.macd_line(df, fast, slow, signal)` |
| `macd_signal_line` | callable `(fast: int = 12, slow: int = 26, signal: int = 9) -> float` | Closure over `df`, calls `indicators.library.macd_signal_line(df, fast, slow, signal)` |
| `macd_histogram` | callable `(fast: int = 12, slow: int = 26, signal: int = 9) -> float` | Closure over `df`, calls `indicators.library.macd_histogram(df, fast, slow, signal)` |
| `close` | `float` | Latest close price |
| `volume` | `float` | Latest volume |
| `price_to_book` | `float` | From `meta["price_to_book"]`, or `inf` if missing |
| `change_pct` | `float` | From `meta["change_pct"]`, or `0.0` if missing |
| `in_watchlist` | `bool` | Whether the symbol is in the big-tech/chip watchlist |
| `industry` | `str` | From `meta["industry"]`, or `""` if missing |
| `is_chip` | `bool` | `True` if `"semiconductor"` appears in `industry` (case-insensitive) |

Each indicator is a **closure** (not a partially-applied function) so that asteval can call it with just the period argument, matching the natural syntax `rsi(14)` in the condition string.

A fresh copy of the symbol table (`dict(symbol_table)`) is passed to each `Interpreter` instance so that one rule's side-effects (asteval may write intermediate values into its symtable) cannot affect another rule's evaluation.

---

## `score()` Formula

```
score(results: list[RuleResult]) -> float
```

```
score = Σ weight(r) for r in results if r.passed
        ─────────────────────────────────────────
               Σ weight(r) for r in results
```

- Returns `0.0` if `results` is empty (no division by zero).
- Returns a value in `[0.0, 1.0]`.
- Higher weight rules contribute proportionally more to the score.

Example with spec weights (2.0 + 0.6 + 1.5 + 1.0 + 1.0 + 0.5 + 1.5 = 8.1 total: `big_tech_or_chip`, `oversold_band`, `quality_uptrend`, `medium_term_momentum`, `macd_bullish`, `near_52w_low`, `undervalued_pb`):
- All 7 pass → score = 1.0
- Only `near_52w_low` passes → score = 0.5 / 8.1 ≈ 0.0617

---

## `detail` Dict — What Gets Captured and Why

`_extract_detail()` captures the indicator scalars referenced in a rule's condition string so downstream consumers (the output JSON writer, the `--ticker` debug view) can show exactly what values drove the pass/fail decision.

**Always included:**
- `close` — latest close price
- `volume` — latest volume
- `in_watchlist` — watchlist membership (cheap scalar, useful context)
- `is_chip` — semiconductor-industry flag (cheap scalar, useful context)
- `price_to_book` — latest price-to-book ratio (cheap scalar, useful context)

**Conditionally included** (when the indicator name appears in the condition string):
- `sma_<period>` — e.g. `sma_50`, `sma_200`
- `ema_<period>`
- `rsi_<period>` — e.g. `rsi_14`
- `atr_<period>` — e.g. `atr_14`
- `sma_volume_<period>` — e.g. `sma_volume_20`
- `low_52w_<period>` — e.g. `low_52w_252`
- `high_52w_<period>` — e.g. `high_52w_252`

Values are rounded to 4 decimal places. Parsing uses a simple regex `name\((\d+)\)` — sufficient for period-parameterized indicators.

**Conditionally included, zero-arg MACD functions** (when the function name with empty parens appears in the condition string, e.g. `macd_line()`): `macd_line`, `macd_signal_line`, `macd_histogram`. These are handled in a separate block in `_extract_detail()` since the period-regex above expects a literal integer argument and can't match empty parens — each is only added if its name is actually referenced in the condition, mirroring the period-indicator loop's "only what's used" behavior. Values are rounded to 4 decimal places using the same try/except-pass safety as the period-based loop.

**Error case:** If `aeval.error` is non-empty or the outcome is `None`, the detail dict contains `{"error": [...]}` instead of indicator values.

---

## How to Add a New Rule

1. Open `config/rules.yaml`.
2. Add an entry under `rules:`:

```yaml
rules:
  - name: my_new_rule
    weight: 1.0
    condition: "ema(20) > sma(50)"
```

3. The condition string may use any of: `sma(n)`, `ema(n)`, `rsi(n)`, `atr(n)`, `sma_volume(n)`, `low_52w(n)`, `high_52w(n)`, `macd_line()`, `macd_signal_line()`, `macd_histogram()`, `close`, `volume`, `price_to_book`, `change_pct`, `in_watchlist`, `industry`, `is_chip`, and standard Python arithmetic/comparison operators.
4. No code changes required — `RuleEngine` reads the list from the loaded `RulesConfig` at instantiation.
5. Add a unit test in `tests/test_rules.py` exercising the new condition against synthetic bars.

