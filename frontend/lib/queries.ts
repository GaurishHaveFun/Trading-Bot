import { sql } from "./db";
import type {
  BacktestDetail,
  BacktestRow,
  BacktestTradeRow,
  BarRow,
  RuleAttributionRow,
  RuleResultRow,
  RunListItem,
  RunRow,
  RunWithSignals,
  SignalRow,
  SignalWithRules,
  TickerHistory,
} from "./types";

/**
 * All read queries for the dashboard, using the Neon `sql` tagged template
 * only. Every interpolation below is a template placeholder, which the
 * Neon driver parameterizes for us — never string-concatenate values into
 * SQL text.
 *
 * The underlying HTTP driver returns `bigint`/`numeric` Postgres columns as
 * JS strings (to avoid precision loss), so numeric fields are coerced with
 * `Number(...)` when we build the typed rows below.
 */

function num(value: unknown): number {
  if (value === null || value === undefined) return 0;
  const n = Number(value);
  return Number.isNaN(n) ? 0 : n;
}

function numOrNull(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  const n = Number(value);
  return Number.isNaN(n) ? null : n;
}

/**
 * The Neon HTTP driver returns Postgres `timestamp`/`timestamptz`/`date`
 * columns as native JS `Date` objects at runtime, even though our types
 * declare these fields as `string`. Interpolating a `Date` into a template
 * literal (e.g. a URL) silently calls its default `.toString()` (producing
 * something like "Sun Jul 19 2026 23:34:05 GMT+0000 (Coordinated Universal
 * Time)") instead of `.toISOString()`, which then fails downstream when
 * used as a query parameter. Route every date/timestamp field through this
 * helper so callers always get a real ISO-8601 string, regardless of
 * whether the driver handed back a Date or (defensively) already a string.
 */
function toIso(value: unknown): string {
  if (value instanceof Date) return value.toISOString();
  return String(value);
}

function toIsoOrNull(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  return toIso(value);
}

// ---------------------------------------------------------------------------
// Row mappers — coerce raw driver rows into our typed shapes.
// ---------------------------------------------------------------------------

function mapRun(row: Record<string, unknown>): RunRow {
  return {
    id: num(row.id),
    run_timestamp: toIso(row.run_timestamp),
    universe: (row.universe as string) ?? null,
    alert_threshold: numOrNull(row.alert_threshold),
    signal_count: numOrNull(row.signal_count),
    created_at: toIsoOrNull(row.created_at),
  };
}

function mapSignal(row: Record<string, unknown>): SignalRow {
  return {
    id: num(row.id),
    run_id: num(row.run_id),
    ticker: String(row.ticker),
    timestamp: toIso(row.timestamp),
    score: num(row.score),
    rules_passed: numOrNull(row.rules_passed),
    rules_total: numOrNull(row.rules_total),
    snapshot: (row.snapshot as SignalRow["snapshot"]) ?? null,
  };
}

function mapRuleResult(row: Record<string, unknown>): RuleResultRow {
  return {
    id: num(row.id),
    signal_id: num(row.signal_id),
    rule_name: String(row.rule_name),
    passed: Boolean(row.passed),
    weight: numOrNull(row.weight),
    detail: (row.detail as RuleResultRow["detail"]) ?? null,
  };
}

function mapBar(row: Record<string, unknown>): BarRow {
  return {
    ticker: String(row.ticker),
    timestamp: toIso(row.timestamp),
    open: num(row.open),
    high: num(row.high),
    low: num(row.low),
    close: num(row.close),
    volume: num(row.volume),
  };
}

function mapBacktest(row: Record<string, unknown>): BacktestRow {
  return {
    id: num(row.id),
    created_at: toIso(row.created_at),
    universe: (row.universe as string) ?? null,
    holding_days: numOrNull(row.holding_days),
    alert_threshold: numOrNull(row.alert_threshold),
    lookback_days: numOrNull(row.lookback_days),
    start_date: toIsoOrNull(row.start_date),
    end_date: toIsoOrNull(row.end_date),
    total_signals: numOrNull(row.total_signals),
    wins: numOrNull(row.wins),
    losses: numOrNull(row.losses),
    win_rate: numOrNull(row.win_rate),
    avg_return_pct: numOrNull(row.avg_return_pct),
    total_return_pct: numOrNull(row.total_return_pct),
    best_trade_return_pct: numOrNull(row.best_trade_return_pct),
    worst_trade_return_pct: numOrNull(row.worst_trade_return_pct),
    baseline_avg_return_pct: numOrNull(row.baseline_avg_return_pct),
  };
}

function mapBacktestTrade(row: Record<string, unknown>): BacktestTradeRow {
  return {
    id: num(row.id),
    backtest_id: num(row.backtest_id),
    ticker: String(row.ticker),
    signal_date: toIso(row.signal_date),
    score: numOrNull(row.score),
    rules_passed: numOrNull(row.rules_passed),
    rules_total: numOrNull(row.rules_total),
    buy_close: numOrNull(row.buy_close),
    sell_date: toIsoOrNull(row.sell_date),
    sell_close: numOrNull(row.sell_close),
    return_pct: numOrNull(row.return_pct),
    win: row.win === null || row.win === undefined ? null : Boolean(row.win),
  };
}

function mapRuleAttribution(row: Record<string, unknown>): RuleAttributionRow {
  return {
    id: num(row.id),
    backtest_id: num(row.backtest_id),
    rule_name: String(row.rule_name),
    weight: numOrNull(row.weight),
    passed_count: numOrNull(row.passed_count),
    passed_win_rate: numOrNull(row.passed_win_rate),
    passed_avg_return_pct: numOrNull(row.passed_avg_return_pct),
    failed_count: numOrNull(row.failed_count),
    failed_win_rate: numOrNull(row.failed_win_rate),
    failed_avg_return_pct: numOrNull(row.failed_avg_return_pct),
    edge_pct: numOrNull(row.edge_pct),
  };
}

// ---------------------------------------------------------------------------
// Shared helper: attach nested signals + rule_results to a run row.
// ---------------------------------------------------------------------------

async function attachSignals(run: RunRow): Promise<RunWithSignals> {
  const signalRows = await sql`
    SELECT id, run_id, ticker, timestamp, score, rules_passed, rules_total, snapshot
    FROM signals
    WHERE run_id = ${run.id}
    ORDER BY score DESC
  `;
  const signals = signalRows.map((r) => mapSignal(r as Record<string, unknown>));

  if (signals.length === 0) {
    return { ...run, signals: [] };
  }

  const signalIds = signals.map((s) => s.id);
  const ruleRows = await sql`
    SELECT id, signal_id, rule_name, passed, weight, detail
    FROM rule_results
    WHERE signal_id = ANY(${signalIds})
  `;
  const ruleResults = ruleRows.map((r) => mapRuleResult(r as Record<string, unknown>));

  const rulesBySignal = new Map<number, RuleResultRow[]>();
  for (const rr of ruleResults) {
    const list = rulesBySignal.get(rr.signal_id) ?? [];
    list.push(rr);
    rulesBySignal.set(rr.signal_id, list);
  }

  const signalsWithRules: SignalWithRules[] = signals.map((s) => ({
    ...s,
    rule_results: rulesBySignal.get(s.id) ?? [],
  }));

  return { ...run, signals: signalsWithRules };
}

// ---------------------------------------------------------------------------
// Public query functions
// ---------------------------------------------------------------------------

/** Most recent run plus its signals (score DESC) each with nested rule_results. */
export async function getLatestRun(): Promise<RunWithSignals | null> {
  const rows = await sql`
    SELECT id, run_timestamp, universe, alert_threshold, signal_count, created_at
    FROM runs
    ORDER BY run_timestamp DESC
    LIMIT 1
  `;
  if (rows.length === 0) return null;
  const run = mapRun(rows[0] as Record<string, unknown>);
  return attachSignals(run);
}

/** Past runs, most recent first, each augmented with a count of signals >= alert_threshold. */
export async function listRuns(limit = 50): Promise<RunListItem[]> {
  const rows = await sql`
    SELECT
      r.id,
      r.run_timestamp,
      r.universe,
      r.alert_threshold,
      r.signal_count,
      r.created_at,
      COALESCE(above.cnt, 0) AS above_threshold_count
    FROM runs r
    LEFT JOIN LATERAL (
      SELECT COUNT(*) AS cnt
      FROM signals s
      WHERE s.run_id = r.id AND s.score >= r.alert_threshold
    ) above ON true
    ORDER BY r.run_timestamp DESC
    LIMIT ${limit}
  `;
  return rows.map((row) => {
    const r = row as Record<string, unknown>;
    return { ...mapRun(r), above_threshold_count: num(r.above_threshold_count) };
  });
}

/**
 * One specific run by run_timestamp match, with nested signals + rule_results.
 *
 * `run_timestamp` is stored with microsecond precision, but the value we
 * hand out to the browser (via `toIso` above, and JS `Date`/`toISOString`
 * generally) only has millisecond precision — the sub-millisecond digits
 * are truncated. An exact string/timestamptz equality would therefore
 * silently fail to find the row for almost every run. Truncate the stored
 * column to milliseconds before comparing so it matches what round-trips
 * through the browser.
 */
export async function getRun(timestamp: string): Promise<RunWithSignals | null> {
  const rows = await sql`
    SELECT id, run_timestamp, universe, alert_threshold, signal_count, created_at
    FROM runs
    WHERE date_trunc('milliseconds', run_timestamp) = ${timestamp}::timestamptz
    LIMIT 1
  `;
  if (rows.length === 0) return null;
  const run = mapRun(rows[0] as Record<string, unknown>);
  return attachSignals(run);
}

/**
 * A ticker's signal history across all runs (each joined back to its parent
 * run for context), plus that ticker's bars for charting.
 */
export async function getTickerHistory(ticker: string): Promise<TickerHistory> {
  const [signalRows, barRows] = await Promise.all([
    sql`
      SELECT
        s.id, s.run_id, s.ticker, s.timestamp, s.score, s.rules_passed, s.rules_total, s.snapshot,
        r.run_timestamp AS run_run_timestamp,
        r.universe AS run_universe,
        r.alert_threshold AS run_alert_threshold
      FROM signals s
      JOIN runs r ON r.id = s.run_id
      WHERE s.ticker = ${ticker}
      ORDER BY s.timestamp DESC
    `,
    sql`
      SELECT ticker, timestamp, open, high, low, close, volume
      FROM bars
      WHERE ticker = ${ticker}
      ORDER BY timestamp ASC
    `,
  ]);

  const signals = signalRows.map((row) => {
    const r = row as Record<string, unknown>;
    return {
      signal: mapSignal(r),
      run: {
        run_timestamp: toIso(r.run_run_timestamp),
        universe: (r.run_universe as string) ?? null,
        alert_threshold: numOrNull(r.run_alert_threshold),
      },
    };
  });

  const bars = barRows.map((r) => mapBar(r as Record<string, unknown>));

  return { ticker, signals, bars };
}

/** Past backtests, most recent first. */
export async function listBacktests(limit = 50): Promise<BacktestRow[]> {
  const rows = await sql`
    SELECT
      id, created_at, universe, holding_days, alert_threshold, lookback_days,
      start_date, end_date, total_signals, wins, losses, win_rate, avg_return_pct,
      total_return_pct, best_trade_return_pct, worst_trade_return_pct, baseline_avg_return_pct
    FROM backtests
    ORDER BY created_at DESC
    LIMIT ${limit}
  `;
  return rows.map((r) => mapBacktest(r as Record<string, unknown>));
}

/** One backtest plus its trades and rule attributions. */
export async function getBacktest(id: number): Promise<BacktestDetail | null> {
  const rows = await sql`
    SELECT
      id, created_at, universe, holding_days, alert_threshold, lookback_days,
      start_date, end_date, total_signals, wins, losses, win_rate, avg_return_pct,
      total_return_pct, best_trade_return_pct, worst_trade_return_pct, baseline_avg_return_pct
    FROM backtests
    WHERE id = ${id}
    LIMIT 1
  `;
  if (rows.length === 0) return null;
  const backtest = mapBacktest(rows[0] as Record<string, unknown>);

  const [tradeRows, attributionRows] = await Promise.all([
    sql`
      SELECT id, backtest_id, ticker, signal_date, score, rules_passed, rules_total,
             buy_close, sell_date, sell_close, return_pct, win
      FROM backtest_trades
      WHERE backtest_id = ${id}
      ORDER BY signal_date ASC
    `,
    sql`
      SELECT id, backtest_id, rule_name, weight, passed_count, passed_win_rate,
             passed_avg_return_pct, failed_count, failed_win_rate, failed_avg_return_pct, edge_pct
      FROM rule_attributions
      WHERE backtest_id = ${id}
      ORDER BY edge_pct DESC NULLS LAST
    `,
  ]);

  return {
    ...backtest,
    trades: tradeRows.map((r) => mapBacktestTrade(r as Record<string, unknown>)),
    rule_attributions: attributionRows.map((r) => mapRuleAttribution(r as Record<string, unknown>)),
  };
}
