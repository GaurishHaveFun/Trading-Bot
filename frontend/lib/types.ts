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

// ---------------------------------------------------------------------------
// Paper trading (single-user virtual account). Schema owned by the frontend
// itself (see lib/paper-schema.ts), unlike the tables above which mirror the
// Python backend's screener schema.
// ---------------------------------------------------------------------------

export interface PaperAccountRow {
  id: number;
  cash_balance: number;
  starting_balance: number;
  created_at: string;
  updated_at: string;
}

export interface PaperPositionRow {
  ticker: string;
  quantity: number;
  avg_cost: number;
  updated_at: string;
}

export interface PaperTradeRow {
  id: number;
  ticker: string;
  side: "buy" | "sell";
  quantity: number;
  price: number;
  realized_pnl: number | null;
  executed_at: string;
}

/** A snapshot of total account value, for the account-value-over-time chart. */
export interface PaperAccountHistoryRow {
  id: number;
  total_value: number;
  cash_balance: number;
  positions_value: number;
  recorded_at: string;
}

// ---------------------------------------------------------------------------
// Screener FastAPI client types (backend/src/screener/api/app.py). These come
// back as real JSON numbers from FastAPI, not Postgres numeric-as-string, so
// no num()/numOrNull() coercion is needed for these shapes.
// ---------------------------------------------------------------------------

/** GET /quotes response row. */
export interface QuoteRow {
  ticker: string;
  price: number | null;
  change_pct: number | null;
  previous_close: number | null;
  currency: string;
}

/** GET /losers response row. */
export interface LoserRow {
  ticker: string;
  price: number | null;
  change_pct: number | null;
  market_cap: number | null;
  sector: string | null;
}

/** GET /tickers/{symbol} response.profile — mirrors TickerProfileOut. */
export interface TickerProfile {
  long_name: string | null;
  short_name: string | null;
  business_summary: string | null;
  sector: string | null;
  industry: string | null;
  employees: number | null;
  website: string | null;
  country: string | null;
  exchange: string | null;
  currency: string | null;
}

/** GET /tickers/{symbol} response.stats — mirrors TickerStatsOut. */
export interface TickerStats {
  price: number | null;
  change_pct: number | null;
  previous_close: number | null;
  open: number | null;
  day_high: number | null;
  day_low: number | null;
  market_cap: number | null;
  trailing_pe: number | null;
  forward_pe: number | null;
  price_to_book: number | null;
  eps_trailing: number | null;
  eps_forward: number | null;
  dividend_yield: number | null;
  beta: number | null;
  fifty_two_week_high: number | null;
  fifty_two_week_low: number | null;
  volume: number | null;
  avg_volume: number | null;
  avg_volume_10d: number | null;
}

/** GET /tickers/{symbol} response.targets — mirrors TickerTargetsOut. */
export interface TickerTargets {
  mean: number | null;
  high: number | null;
  low: number | null;
  median: number | null;
  recommendation_key: string | null;
  analyst_count: number | null;
}

/** GET /tickers/{symbol} response — mirrors TickerDetailOut. */
export interface TickerDetail {
  ticker: string;
  profile: TickerProfile;
  stats: TickerStats;
  targets: TickerTargets;
  bars: BarRow[];
}
