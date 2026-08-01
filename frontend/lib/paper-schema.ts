import { sql } from "./db";

/**
 * Idempotent schema (+ seed) for the single-user paper-trading account.
 *
 * Unlike lib/queries.ts, which only *reads* a schema owned by the Python
 * backend (see backend/src/screener/output/db_writer.py), this module
 * *owns* the paper-trading tables on the frontend side. It follows the same
 * idempotent-DDL idiom as db_writer.py: plain `CREATE TABLE IF NOT EXISTS` /
 * `CREATE INDEX IF NOT EXISTS` statements, safe to run on every call, no
 * separate migration framework.
 *
 * This is schema + seeding ONLY. Buy/sell server actions and read queries
 * (getAccount/getPositions/getTrades) are a later step and do not live here.
 *
 * Design choice: `ensurePaperSchema` and `ensureSeededAccount` are kept as
 * two separate exports (rather than folding seeding into the schema
 * function) so callers that only need the tables to exist (e.g. a future
 * read query) aren't forced to also pay for the "is the account seeded"
 * check on every call. `ensureSeededAccount` calls `ensurePaperSchema`
 * itself first, so callers that DO want both only need to call the one
 * function.
 */

/**
 * Idempotently creates the paper-trading tables (`paper_account`,
 * `paper_positions`, `paper_trades`) and their indexes. Safe to call on
 * every request/write, matching the `db_writer.py` idiom on the Python side.
 *
 * Each statement is a separate tagged-template call (rather than a shared
 * array of plain strings run through some generic executor) because the
 * exported `sql` client here (see lib/db.ts) only implements the tagged-
 * template call signature at runtime, not `sql.query(text)`.
 */
export async function ensurePaperSchema(): Promise<void> {
  await sql`
    CREATE TABLE IF NOT EXISTS paper_account (
      id SERIAL PRIMARY KEY,
      cash_balance NUMERIC NOT NULL,
      starting_balance NUMERIC NOT NULL DEFAULT 100000,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
  `;

  await sql`
    CREATE TABLE IF NOT EXISTS paper_positions (
      ticker TEXT PRIMARY KEY,
      quantity NUMERIC NOT NULL,
      avg_cost NUMERIC NOT NULL,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
  `;

  await sql`
    CREATE TABLE IF NOT EXISTS paper_trades (
      id SERIAL PRIMARY KEY,
      ticker TEXT NOT NULL,
      side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
      quantity NUMERIC NOT NULL,
      price NUMERIC NOT NULL,
      realized_pnl NUMERIC NULL,
      executed_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
  `;

  await sql`
    CREATE INDEX IF NOT EXISTS idx_paper_trades_executed_at
    ON paper_trades (executed_at DESC)
  `;

  await sql`
    CREATE TABLE IF NOT EXISTS paper_account_history (
      id SERIAL PRIMARY KEY,
      total_value NUMERIC NOT NULL,
      cash_balance NUMERIC NOT NULL,
      positions_value NUMERIC NOT NULL,
      recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
  `;

  await sql`
    CREATE INDEX IF NOT EXISTS idx_paper_account_history_recorded_at
    ON paper_account_history (recorded_at)
  `;
}

/**
 * Ensures the schema exists, then seeds the single `paper_account` row
 * (cash_balance = starting_balance = 100000) if the table is empty.
 * Safe to call repeatedly — a second call is a no-op once the row exists.
 */
export async function ensureSeededAccount(): Promise<void> {
  await ensurePaperSchema();

  const rows = await sql`SELECT id FROM paper_account LIMIT 1`;
  if (rows.length > 0) return;

  await sql`
    INSERT INTO paper_account (cash_balance, starting_balance)
    VALUES (100000, 100000)
  `;
}
