import { sql } from "./db";

/**
 * Idempotent schema for the single-user "model picks" table — tickers the
 * user has personally flagged as ones they'd actually buy, reviewed
 * ticker-by-ticker on the screener detail pages. Mirrors the
 * idempotent-DDL idiom in lib/paper-schema.ts (plain `CREATE TABLE IF NOT
 * EXISTS`, safe to call on every request, no separate migration framework).
 *
 * This table is deliberately minimal (just ticker + when it was added) —
 * the "why" lives implicitly in the screener snapshot for that ticker at
 * read time (see lib/patterns.ts), not duplicated onto this row.
 */
export async function ensureModelSchema(): Promise<void> {
  await sql`
    CREATE TABLE IF NOT EXISTS model_picks (
      ticker TEXT PRIMARY KEY,
      added_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
  `;
}
