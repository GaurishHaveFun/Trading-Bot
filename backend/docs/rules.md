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
evaluate(symbol, bars: list[Bar]) -> list[RuleResult]
```

1. Convert `bars` to a sorted pandas DataFrame via `_bars_to_df()`.
2. Extract `close` (latest close scalar) and `volume` (latest volume scalar) via `latest_close()` / `latest_volume()` from the indicators library.
3. Call `build_symbol_table(df, close, volume)` to build a fresh symbol dict.
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
build_symbol_table(df: pd.DataFrame, close: float, volume: float) -> dict
```

| Key | Type | Description |
|-----|------|-------------|
| `sma` | callable `(period: int) -> float` | Closure over `df`, calls `indicators.library.sma(df, period)` |
| `ema` | callable `(period: int) -> float` | Closure over `df`, calls `indicators.library.ema(df, period)` |
| `rsi` | callable `(period: int) -> float` | Closure over `df`, calls `indicators.library.rsi(df, period)` |
| `atr` | callable `(period: int) -> float` | Closure over `df`, calls `indicators.library.atr(df, period)` |
| `sma_volume` | callable `(period: int) -> float` | Closure over `df`, calls `indicators.library.sma_volume(df, period)` |
| `close` | `float` | Latest close price |
| `volume` | `float` | Latest volume |

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

Example with spec weights (2.0 + 1.5 + 1.5 + 1.0 + 1.0 = 7.0 total):
- All 5 pass → score = 1.0
- Only `reasonable_volatility` passes → score = 1.0 / 7.0 ≈ 0.143

---

## `detail` Dict — What Gets Captured and Why

`_extract_detail()` captures the indicator scalars referenced in a rule's condition string so downstream consumers (the output JSON writer, the `--ticker` debug view) can show exactly what values drove the pass/fail decision.

**Always included:**
- `close` — latest close price
- `volume` — latest volume

**Conditionally included** (when the indicator name appears in the condition string):
- `sma_<period>` — e.g. `sma_50`, `sma_200`
- `ema_<period>`
- `rsi_<period>` — e.g. `rsi_14`
- `atr_<period>` — e.g. `atr_14`
- `sma_volume_<period>` — e.g. `sma_volume_20`

Values are rounded to 4 decimal places. Parsing uses a simple regex `name\((\d+)\)` — sufficient for the current rule grammar.

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

3. The condition string may use any of: `sma(n)`, `ema(n)`, `rsi(n)`, `atr(n)`, `sma_volume(n)`, `close`, `volume`, and standard Python arithmetic/comparison operators.
4. No code changes required — `RuleEngine` reads the list from the loaded `RulesConfig` at instantiation.
5. Add a unit test in `tests/test_rules.py` exercising the new condition against synthetic bars.
