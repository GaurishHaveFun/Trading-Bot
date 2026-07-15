# Feature: "Buy the Quality Dip" — Daily Losers Screener

## Overview
Add a buy-the-dip strategy on top of Phase 1. Instead of screening a fixed 10-symbol list, pull the **top daily losers** from yfinance and score them for a buy-the-dip play that favors **big-tech / chip names** that are **down today**, in a **long-term uptrend**, **oversold but not in freefall**, **near their 52-week low**, and optionally **cheap on price/book**.

## Architecture Summary
Minimal surgical changes to existing architecture. One new provider file plus small localized injections along the rule path, and a rewritten `rules.yaml`. No new architectural layers.

## New Files

### `src/screener/universe/losers.py` — `LosersUniverse`
- `get_symbols()`: calls `yf.screen("day_losers", count=~50)`, drops `marketCap < $10B` and non-EQUITY quoteTypes, takes top 15 by % loss, unions with watchlist symbols that are down today.
- `get_quotes() -> dict[str, dict]`: stashes per-symbol metadata (`price_to_book`, `change_pct`, `market_cap`) for the engine. Fetched once per run.

### `config/watchlist.yaml`
Big-tech + chip symbols: AAPL, MSFT, GOOGL, AMZN, META, NVDA, TSLA, AMD, AVGO, TSM, INTC, QCOM, MU, ASML, ARM, SMCI.

## Edited Files (all small, localized)

| File | Change |
|------|--------|
| `src/screener/universe/base.py` | Add `get_quotes(self) -> dict[str, dict]` with default `return {}` so existing providers are unaffected |
| `src/screener/indicators/library.py` | Add `low_52w(df, period=252)` and `high_52w(df, period=252)` (rolling min/max, latest scalar) |
| `src/screener/rules/functions.py` | Extend `build_symbol_table(df, close, volume, meta=None, in_watchlist=False)` to inject `low_52w`/`high_52w` callables, scalar `price_to_book` (default `float("inf")`), `change_pct`, and boolean `in_watchlist` |
| `src/screener/rules/engine.py` | `evaluate(symbol, bars, meta=None, watchlist=None)`: compute `in_watchlist = symbol in watchlist`, pass meta scalars into symbol table; extend `_extract_detail` to surface `in_watchlist`, `price_to_book`, `low_52w` |
| `src/screener/config.py` | Add `load_watchlist(path) -> set[str]` and a `universe` setting (`"static" \| "losers"`, default `"losers"`) plus `watchlist_path` |
| `src/screener/main.py` | Select provider by setting, load watchlist, pull `get_quotes()` once, thread each symbol's meta + watchlist into `engine.evaluate(...)`, add `price_to_book`/`change_pct`/`in_watchlist` to snapshot |
| `config/rules.yaml` | Replace with buy-the-dip rule set (old rules preserved in git history) |

## New Rule Set (`config/rules.yaml`)

| Name | Weight | Condition |
|------|--------|-----------|
| big_tech_or_chip | 2.0 | `in_watchlist` |
| oversold_band | 0.6 | `rsi(14) > 25 and rsi(14) < 40` |
| quality_uptrend | 1.5 | `close > sma(200)` |
| near_52w_low | 0.5 | `close <= low_52w(252) * 1.15` |
| undervalued_pb | 1.5 | `price_to_book < 4` |

**Total weight:** 6.1  
**Alert threshold:** 0.70 → ~4.27 weight needed to trigger  
**Example:** A big-tech name that's oversold, in long uptrend, near its 52w low scores `2.0 + 0.6 + 1.5 + 0.5 = 4.6 / 6.1 = 0.75` → fires alert.

## Scoring Note
P/B and "big tech" fire on different stocks — mega-cap tech has P/B ~40+, so `price_to_book < 4` rarely triggers for watchlist names (it flags value/energy/financials instead). Soft weighted scoring absorbs this: a stock scores well on the rules it hits. The `near_52w_low` rule is the better "quality name on sale" signal for tech. All weights are in `rules.yaml` and tunable.

## Tests

| Test file | What it covers |
|-----------|----------------|
| `tests/test_losers_universe.py` (new) | Mock `yf.screen` returning synthetic losers; assert cap-floor drops penny stocks, top-15 cap, watchlist union, `get_quotes()` shape |
| `tests/test_indicators.py` | Add `low_52w`/`high_52w` cases vs existing fixture |
| `tests/test_rules.py` | Add cases: `in_watchlist` true/false, `price_to_book` default-inf safety, `near_52w_low`, oversold-band boundaries |
| Integration test (skippable) | `@pytest.mark.integration` live `day_losers` fetch |

## Docs (Sonnet subagents)
Update `docs/universe.md`, `docs/rules.md`, `docs/indicators.md`, `docs/overview.md` after implementation.

## Verification Checklist
- [ ] `uv run pytest -m "not integration"` → all pass (existing 59 + new tests)
- [ ] `uv run python -m screener.main --once` with `universe=losers` → JSON in `output/runs/` listing today's down big-tech/chip + value losers, scored
- [ ] `uv run python -m screener.main --ticker NVDA` → per-rule breakdown showing `in_watchlist=True`, rsi band, sma200, 52w-low, P/B
- [ ] Spot-check: penny-stock losers absent (cap floor), all timestamps UTC, no NaN, JSON logs only

## Effort Summary
Mostly minute details + one new provider file. No new architecture. Largest piece is `losers.py` (~60 lines). Everything else is small injections along an existing path and a config rewrite.
