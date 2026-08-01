# Running the Screener

All commands are run from `backend/`.

## 1. One-time setup

```bash
cd backend
uv sync                  # installs deps into .venv (Python 3.12, pinned via .python-version)
cp .env.example .env     # fill in Schwab credentials below if you want live Schwab data
```

`config.py`'s `Settings` now defaults `data_provider` to `"schwab"` (not `"yfinance"`) and `universe` to `"losers"`. Nothing in `.env` is strictly *required* to run, but for a different reason than before: if `SCHWAB_APP_KEY`/`SCHWAB_APP_SECRET`/a valid token file aren't present, every Schwab call fails and the screener transparently falls back to yfinance — see `screener.data.factory`'s module docstring for the exact per-provider fallback semantics, summarized here:

- **Bars** (`_FallbackBarProvider`): pure try-Schwab-then-fallback, per call. Any Schwab exception for a given symbol/call falls back to yfinance for that call only — a transient Schwab failure for one symbol never takes down the whole run.
- **Losers universe** (`_MergedLosersUniverse`): NOT a strict either/or. See section 3 below — when Schwab is healthy its candidates are *unioned* with yfinance's, not swapped in for them.
- **Fundamentals** (`_MergedFundamentalsProvider`): also a merge, not a strict either/or — yfinance is always the base snapshot, with 2 specific Schwab fields layered on top when available.
- **`quote_for`** (single-symbol lookup used by `--ticker` debug mode): pure try-Schwab-then-fallback, same as bars — this one codepath is intentionally *not* part of the merge semantics above.

So: run it with an empty `.env` and you still get a fully working screener on yfinance alone (graceful degradation, not a hard requirement). Configure Schwab (below) to get real-time movers data, two additional fundamentals fields, and the pre-run market-hours gate.

`ALERT_THRESHOLD` (default `0.70`) can also be overridden in `.env` if you want alerts to fire more or less easily.

### Setting up Schwab (optional, but the default provider)

1. Register an app at [developer.schwab.com](https://developer.schwab.com). Select **Market Data Production** only — this project makes zero trading/order calls (no `/trader/v1/accounts` or order-placement endpoints appear anywhere in the codebase), so there's no need for trading API access.
2. Set the app's callback URL to exactly `https://127.0.0.1:8182` — it must match `.env`'s `SCHWAB_CALLBACK_URL` character-for-character (`schwab-py`'s login flow, which this project uses under the hood, only accepts callback URLs whose host is literally `127.0.0.1`).
3. Copy `.env.example` to `.env` (if you haven't already) and fill in `SCHWAB_APP_KEY` / `SCHWAB_APP_SECRET` from the app you just registered.
4. Run the one-time interactive auth flow:

   ```bash
   uv run python -m screener.main --auth-schwab
   ```

   This opens your browser at Schwab's login/consent page, then redirects back to `https://127.0.0.1:8182`. You'll hit a browser security warning on that redirect (self-signed cert on the local loopback server `schwab-py` stands up to catch it) — click through it, this is expected and normal, not a bug. On success it writes a token file to `SCHWAB_TOKEN_PATH` (default `.cache/schwab_token.json`) and prints `Schwab authorization complete.`

   Re-running `--auth-schwab` always forces a fresh interactive login (it deliberately doesn't silently reuse an existing token file, unlike the runtime client-construction path) — that's intentional, since the whole point of running it by hand is to get a new token.

5. **Token lifetime:** the resulting Schwab access token is short-lived, but `schwab-py`'s underlying `authlib` session auto-refreshes it transparently on every request (rewriting the token file on disk each time) — nothing in this codebase tracks expiry or refreshes by hand. The *refresh* token itself is longer-lived but not indefinite: Schwab's refresh tokens expire after about a week. Once that happens, the auto-refresh stops working and you'll need to run `--auth-schwab` again manually to re-authorize. (`screener.data.schwab.auth`'s module docstring flags the exact live failure mode of a dead refresh token as unverified — no live credentials were exercised against actual expiry — but the fallback machinery means a dead token just degrades every Schwab call to its yfinance fallback rather than crashing a run.)

`DATA_PROVIDER` in `.env` (or the `data_provider` setting) accepts:

- `schwab` (default) — try Schwab first for bars/fundamentals/universe, per the merge/fallback rules above.
- `yfinance` — skip Schwab entirely, identical behavior to this project before the Schwab integration existed.

## 2. The four ways to fire it

### `--once` — single run, writes JSON + PDF

```bash
uv run python -m screener.main --once
```

Runs the full pipeline exactly once: builds the universe → fetches bars and fundamentals → scores every remaining symbol → writes `output/runs/run_<UTC_TIMESTAMP>.json` and `output/reports/report_<UTC_TIMESTAMP>.pdf` → exits. This is what a cron/scheduled invocation actually calls once a day (with one extra gate — see section 4). See section 5 for details on the PDF report.

Console output is structured JSON logs (one line per event: `screener_start`, `fetching_bars`, `insufficient_bars` for skipped tickers, `run_written`, `screener_complete`). The `screener_complete` line summarizes the run:

```json
{"total_symbols": 18, "signals_evaluated": 15, "above_threshold": 0, "top5": ["SYF=0.56", ...], "output": "output/runs/run_..."}
```

`above_threshold` is how many symbols actually crossed `alert_threshold` and would count as a fired alert. It's normal for this to be `0` on any given day — the rule set is a strict multi-factor blend, not every ticker will line up on all 7 rules at once.

### `--ticker SYMBOL` — debug a single ticker

```bash
uv run python -m screener.main --ticker NVDA
```

Fetches and evaluates just that ticker (bars + fundamentals) and prints a readable per-rule breakdown to the console (score, which rules passed/failed, and the indicator values behind each rule). It does not write a JSON file, but does write a single-ticker PDF report to `output/reports/report_<TICKER>_<UTC>.pdf` (path printed after the breakdown). Use this to sanity-check why a specific stock did or didn't score well. Like `--once`, this is a manual invocation and is never gated on market hours (see section 4).

### `--backtest` — historical backtest of the current rules

```bash
uv run python -m screener.main --backtest
uv run python -m screener.main --backtest --days 30 --hold 5   # defaults shown explicitly
```

Answers a different question than `--once`/`--ticker`: not "what fires today," but "over the last N trading days, which watchlist stocks would have passed the rules, and would buying on the signal day have been profitable?" Fetches historical bars for the 16 symbols in `config/watchlist.yaml` (**not** the live `losers` universe — see `backend/docs/backtest.md` for why that universe isn't backtestable), walks each trading day in the trailing `--days` window (default 30) with no look-ahead, scores it against `config/rules.yaml`'s rules **minus `undervalued_pb`** (dropped because price-to-book has no historical series — see the caveats below), and for every day the score is at/above `ALERT_THRESHOLD`, simulates buying at that day's close and selling `--hold` trading days later (default 5).

Writes a standalone report to `output/reports/backtest_<UTC_ISO>.pdf` — a **separate file family** from `report_<UTC>.pdf`/`report_<TICKER>_<UTC>.pdf`; it never overwrites or merges with those. `--once` and `--ticker` behavior is completely unaffected by this mode. Prints a short console summary (signals fired, win rate, average return, and the delta vs. the baseline — see below) plus the PDF path.

The PDF opens with a caveats block (watchlist-only universe, dropped rule, fixed 5-day hold, no transaction costs, small sample) so the numbers aren't over-read, followed by an aggregate stats table and a full per-trade table (green for wins, red for losses). The key number is **signal average return vs. baseline** — the baseline is the average forward return across *every* evaluated symbol-day, signal or not, so the delta answers "did the rules actually add edge over just holding anything?" See `backend/docs/backtest.md` for the full design writeup.

May take up to ~60 seconds on a cold cache (16 symbols × ~500 calendar days of history); the `BarCache` warms it for subsequent runs. Note: the backtest always uses whatever `DATA_PROVIDER` is configured, same fallback rules as everything else.

### Bare — start the cron scheduler

```bash
uv run python -m screener.main
```

Starts an APScheduler loop that calls the same pipeline as `--once` on the cron schedule defined in `config/rules.yaml` (`schedule.on`, `schedule.timezone` — currently `0 16 * * 1-5` / `America/New_York`, i.e. 4pm ET on weekdays). Blocks until you send `SIGINT`/`SIGTERM` (Ctrl-C). Unlike `--once`/`--ticker`, every scheduled tick is gated on the equity market actually being open — see section 4.

There's also a fifth, setup-only mode, `--auth-schwab`, covered in section 1 above — it doesn't run the screener at all, just the one-time Schwab OAuth flow.

## 3. What's actually being screened right now

`config.py` defaults `universe` to `"losers"` (not the original static 10-symbol list) and `data_provider` to `"schwab"`. With both at their defaults, each run:

1. Asks Schwab for today's movers-down (via its `get_movers` endpoint, capped at ~10 symbols by Schwab's own API), drops sub-$10B market cap / non-equity names.
2. **Unions** that with yfinance's own independent `day_losers` top-20 scan (also `$10B`+/equity-filtered) into one combined `{symbol: quote}` pool — Schwab is no longer a pure fallback for this step; when it's healthy its results are merged with yfinance's, not swapped in for them. On a symbol present in both sources, the Schwab-sourced quote wins (see `_MergedLosersUniverse` in `screener.data.factory`). The merged pool is re-ranked by % loss and sliced to the top 20.
3. Also unions in any symbol from `config/watchlist.yaml` (big-tech/chip names) that's down today.
4. Only if Schwab's movers call fails outright or returns nothing does this degrade to a pure yfinance-only top-20 (the old "try Schwab, fall back to yfinance" behavior) — logged as a `provider_fallback` event.
5. Scores the combined set against `config/rules.yaml`'s current **7 rules**:

| Rule | Weight | Condition |
|---|---|---|
| `big_tech_or_chip` | 2.0 | `in_watchlist or is_chip` |
| `oversold_band` | 0.6 | `rsi(14) > 25 and rsi(14) < 40` |
| `quality_uptrend` | 1.5 | `close > sma(200)` |
| `medium_term_momentum` | 1.0 | `sma(50) > sma(100)` |
| `macd_bullish` | 1.0 | `macd_line() > macd_signal_line()` |
| `near_52w_low` | 0.5 | `close <= low_52w(252) * 1.15` |
| `undervalued_pb` | 1.5 | `price_to_book < 4` |

`medium_term_momentum` and `macd_bullish` are new since this doc was last written — both are momentum-timing rules that sit ahead of the older, slower `quality_uptrend` check: `medium_term_momentum` catches the 50/100-day SMA crossing bullish before the 200-day trend confirms it, and `macd_bullish` catches the MACD line crossing above its own 9-day signal line, a classic momentum-turn signal.

To go back to the original fixed 10-ticker universe for a run, set `UNIVERSE=static` in `.env` (or export it) before running. `StaticUniverse` is unaffected by `DATA_PROVIDER` — it never fetches quotes over the network either way.

### `big_tech_or_chip` is sector-aware, not just a watchlist check

The condition is `in_watchlist or is_chip`. `in_watchlist` is membership in the curated `config/watchlist.yaml` list; `is_chip` is derived from the symbol's `industry` string (`"semiconductor" in industry.lower()`), so any semiconductor-industry stock passes this rule even if it isn't in the watchlist — e.g. SKYT (SkyWater Technology) passes via `is_chip` despite never being added to `watchlist.yaml`.

One caveat, now with a Schwab wrinkle: `industry` is reliably present on yfinance `.info` lookups (used by `--ticker` and the watchlist-down-today check) but is often missing on yfinance's `day_losers` screen quotes, and Schwab's quote/reference schema has **no** `industry`/`sector` field at all (confirmed against the real Schwab OpenAPI spec — `ReferenceEquity` simply doesn't carry it). For Schwab-sourced candidates, `screener.data.factory`'s merge layer best-effort backfills `industry`/`sector` with a yfinance `.info` lookup before the union happens; if that backfill also comes up empty, `is_chip` just evaluates to `False` and the symbol falls back to the `in_watchlist` half of the rule.

### Fundamentals field sourcing

**As of the Schwab integration:** yfinance supplies the full `FundamentalsSnapshot` and is always the base/fallback snapshot. When `data_provider=schwab`, `_MergedFundamentalsProvider` additionally layers 2 fields on top, per-field, whenever Schwab provides a non-`None` value for them: `gross_margin`, `net_margin` — pulled from Schwab's `/marketdata/v1/instruments?projection=fundamental` endpoint (`grossMarginTTM`/`netProfitMarginTTM`; these come back as percentages and are divided by 100 to match this project's 0–1 ratio convention). The other fields — `fcf_5y_cumulative`, `interest_coverage`, `ocf_ni_ratio`, `share_dilution_5y` — always come from yfinance regardless of `data_provider`. For `fcf_5y_cumulative`/`ocf_ni_ratio`/`share_dilution_5y` this is a structural Schwab limitation: they need multi-year income-statement/cash-flow history, and Schwab's Trader API only exposes a current/TTM snapshot with no historical time series, confirmed by live testing rather than assumed. `interest_coverage` is different — Schwab's `fundamental` section does return an `interestCoverage` field — but it's an intentional exclusion, not a structural one; see the caveat below.

**Known caveat (why `interest_coverage` is yfinance-only):** live testing found Schwab's `interestCoverage` field returned exactly `0.0` for both AAPL and MSFT, which doesn't look like a real value for either company (neither is a zero-coverage business) and looks like a systematic Schwab data artifact. `_MergedFundamentalsProvider._SCHWAB_FIELDS` therefore excludes `interest_coverage` entirely, so the merged snapshot's `interest_coverage` always comes from yfinance, unaffected by this Schwab data issue. `SchwabFundamentalsProvider` still computes `interest_coverage` in its own (unmerged) snapshot and still logs an `interest_coverage_suspicious_zero` warning whenever the raw Schwab value is exactly `0.0`, so the anomaly stays visible in the logs for diagnostic purposes.

## 4. The pre-run market-hours gate

Scheduled (cron) runs — the bare `uv run python -m screener.main` invocation from section 2 — are gated on the equity market actually being open today. Before each scheduled tick fires the pipeline, `screener.scheduler` calls `run_screener(check_market_hours=True)`, which asks `screener.data.schwab.market_hours.is_equity_market_open` whether the equity market is open right now (via Schwab's `get_market_hours` endpoint). If it reports closed — a weekend, a market holiday — the run is skipped entirely and logged as `market_closed_skip_run`, rather than scoring stale/pre-market data.

**`--once` and `--ticker` are never gated.** Both call `run_screener()`/the ticker-debug path directly with the default `check_market_hours=False`, so they always run regardless of what day or time it is — this gate exists purely to stop the *unattended* cron loop from firing on a day with no fresh market data, not to block manual invocations.

**Fail-open, deliberately:** any failure in the market-hours check itself — Schwab down, auth expired, an unexpected response shape, or simply `data_provider != "schwab"` (no Schwab client to ask) — defaults to `True` (market open, allow the run). The reasoning baked into the code: a missed scheduled run is worse than one unnecessary run on a day the market happened to be closed. This was live-verified on a real Sunday against the real Schwab API — the check correctly returned "closed" and the scheduled run was skipped.

## 5. Where output goes

Every `--once` run (and every scheduler tick) writes to `output/runs/run_<UTC_ISO>.json`, matching the locked schema in `CLAUDE.md`. Nothing is overwritten — each run gets its own timestamped file.

### Output artifacts

Each `--once` run (and scheduler tick) now also writes a human-readable PDF report to `output/reports/report_<UTC_ISO>.pdf` — a ranked summary table plus a full per-ticker rule breakdown, with all numbers formatted cleanly (no raw floats, humanized volume, `—` instead of `inf` for missing P/B). This is purely additive: the JSON schema and file are unchanged. `--ticker SYMBOL` also writes a single-ticker PDF to `output/reports/report_<TICKER>_<UTC_ISO>.pdf` in addition to its terminal breakdown, and prints a one-line pointer to the file (`[PDF] Report written to ...`). See `backend/docs/output.md` for the full `pdf_writer` reference.

Note: the PDF's displayed "Run timestamp" is shown in US Eastern time (`America/New_York`, auto-switching `EST`/`EDT`) for readability — e.g. `Jul 08, 2026 11:08:42 PM EDT`. This is a display-only conversion; the JSON file, `output/runs/`/`output/reports/` filenames, and everything internal remain UTC per the spec.

## 6. Known noise (not bugs)

- yfinance sometimes prints `possibly delisted; no price data found` to stderr during the cache's incremental head/tail fetch (e.g. asking for a single day just outside a ticker's existing cached range). It self-corrects on the next fetch — the run still completes and produces correct data.
- Tickers with fewer than 200 cached bars are logged as `insufficient_bars` and skipped — this is intentional (non-negotiable #5: never let a `Signal` carry NaN indicators from too little history).
- Schwab's movers-down screen can legitimately return **0** candidates on non-trading days — it's real-time, so a Sunday query for "today's losers" has nothing to report (confirmed live on a real Sunday). yfinance's `day_losers` screen, by contrast, appears to silently keep serving the last real trading day's results instead of also going empty. This is why the merged losers universe's composition varies day to day — sometimes a real Schwab+yfinance mix, sometimes effectively all-yfinance — without that variation being a bug; it's just the two sources disagreeing about what "today" means when the market's closed.

## 7. GitHub Actions — daily publish to GitHub Pages

`.github/workflows/screener.yml` (repo root, not `backend/`) runs the screener on a schedule and publishes the PDF report to GitHub Pages so it's viewable without cloning the repo.

- **Schedule:** two daily cron triggers, `0 17 * * *` and `0 21 * * *` UTC. The workflow comment notes GitHub cron is UTC-only (no DST awareness), so these land at 12pm/4pm EST or 1pm/5pm EDT depending on the time of year, drifting an hour across the year; the comment flags `"0 16 * * *"` / `"0 20 * * *"` as the summer-accurate 12pm/4pm ET alternative if that ever matters.
- **What it invokes:** after `uv sync` (`backend/`, Python 3.12 via `astral-sh/setup-uv@v4`), the default (no-input) path runs `uv run python -m screener.main --once`, copies the newest `output/reports/report_[0-9]*.pdf` to `site/daily.pdf`, and writes a small `site/index.html` wrapper (dark-themed, embeds the PDF in an `<iframe>` plus a direct download link) with the run's UTC timestamp in the page.
- **Manual trigger (`workflow_dispatch`):** supports one input, `ticker` — optional, string, default `""`. Leave it blank to run the full daily screen. If set (e.g. `AAPL`), the workflow uppercases it and runs `uv run python -m screener.main --ticker "$TICKER"` instead, then copies the resulting `output/reports/report_<TICKER>_*.pdf` to `site/ticker.pdf` and writes `site/ticker.html` (same style, labeled "Single-ticker report — manual run, not the daily screen").
- **Publishing:** the `Publish to GitHub Pages` step uses `peaceiris/actions-gh-pages@v4` with `publish_dir: ./site` and `keep_files: true` (so a manual single-ticker run doesn't wipe out the last daily `index.html`/`daily.pdf`, and vice versa), authenticated via the built-in `secrets.GITHUB_TOKEN`.
- **Concurrency:** the job runs under `concurrency: { group: screener-pages, cancel-in-progress: false }`, so overlapping runs (e.g. a manual trigger while the daily cron is still running) queue instead of cancelling each other.
- **No Schwab credentials in CI:** the workflow does not set `SCHWAB_APP_KEY`, `SCHWAB_APP_SECRET`, or any other `SCHWAB_*` env var/secret. That means every run of this workflow — scheduled or manual — has `data_provider` defaulting to `"schwab"` but no way to actually reach Schwab, so it falls back to yfinance for everything (bars, universe, fundamentals) on every single call. Local/manual runs on a machine with `.env` configured can use real Schwab data; the published GitHub Pages report currently cannot.

## 8. Tests

```bash
uv run pytest                      # everything, including live-network integration tests
uv run pytest -m "not integration" # fast, no network — what CI/local dev should run day to day
```
