/**
 * Shared TypeScript types for the read-only dashboard.
 *
 * These mirror the Postgres schema owned by the Python backend (see
 * PLAN.md / the DB-write path). We only read from this schema — treat
 * JSONB columns as flexible bags, not exhaustive shapes.
 */

/** Flexible bag of fields living in signals.snapshot (JSONB). Not exhaustive. */
export interface SignalSnapshot {
  close?: number;
  volume?: number;
  rsi_14?: number;
  sma_50?: number;
  sma_200?: number;
  atr_14?: number;
  price_to_book?: number;
  change_pct?: number;
  in_watchlist?: boolean;
  industry?: string;
  is_chip?: boolean;
  [key: string]: unknown;
}

/** Flexible bag of fields living in rule_results.detail (JSONB). Not exhaustive. */
export type RuleResultDetail = Record<string, unknown>;

export interface RunRow {
  id: number;
  run_timestamp: string;
  universe: string | null;
  alert_threshold: number | null;
  signal_count: number | null;
  created_at: string | null;
}

/** A run row from listRuns, augmented with a count of signals above threshold. */
export interface RunListItem extends RunRow {
  above_threshold_count: number;
}

export interface RuleResultRow {
  id: number;
  signal_id: number;
  rule_name: string;
  passed: boolean;
  weight: number | null;
  detail: RuleResultDetail | null;
}

export interface SignalRow {
  id: number;
  run_id: number;
  ticker: string;
  timestamp: string;
  score: number;
  rules_passed: number | null;
  rules_total: number | null;
  snapshot: SignalSnapshot | null;
}

/** A signal with its nested rule_results, as used on the run detail pages. */
export interface SignalWithRules extends SignalRow {
  rule_results: RuleResultRow[];
}

/** A full run with its nested signals (each with nested rule_results). */
export interface RunWithSignals extends RunRow {
  signals: SignalWithRules[];
}

export interface BarRow {
  ticker: string;
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

/** A ticker's signal history entry, joined back to its parent run. */
export interface TickerSignalHistoryItem {
  signal: SignalRow;
  run: Pick<RunRow, "run_timestamp" | "universe" | "alert_threshold">;
}

export interface TickerHistory {
  ticker: string;
  signals: TickerSignalHistoryItem[];
  bars: BarRow[];
}

export interface BacktestRow {
  id: number;
  created_at: string;
  universe: string | null;
  holding_days: number | null;
  alert_threshold: number | null;
  lookback_days: number | null;
  start_date: string | null;
  end_date: string | null;
  total_signals: number | null;
  wins: number | null;
  losses: number | null;
  win_rate: number | null;
  avg_return_pct: number | null;
  total_return_pct: number | null;
  best_trade_return_pct: number | null;
  worst_trade_return_pct: number | null;
  baseline_avg_return_pct: number | null;
}

export interface BacktestTradeRow {
  id: number;
  backtest_id: number;
  ticker: string;
  signal_date: string;
  score: number | null;
  rules_passed: number | null;
  rules_total: number | null;
  buy_close: number | null;
  sell_date: string | null;
  sell_close: number | null;
  return_pct: number | null;
  win: boolean | null;
}

export interface RuleAttributionRow {
  id: number;
  backtest_id: number;
  rule_name: string;
  weight: number | null;
  passed_count: number | null;
  passed_win_rate: number | null;
  passed_avg_return_pct: number | null;
  failed_count: number | null;
  failed_win_rate: number | null;
  failed_avg_return_pct: number | null;
  edge_pct: number | null;
}

export interface BacktestDetail extends BacktestRow {
  trades: BacktestTradeRow[];
  rule_attributions: RuleAttributionRow[];
}
