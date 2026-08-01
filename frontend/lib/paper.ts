"use server";

import { revalidatePath } from "next/cache";
import { sql } from "./db";
import { ensurePaperSchema, ensureSeededAccount } from "./paper-schema";
import { getQuotes } from "./quotes";
import { num, numOrNull } from "./queries";
import type { PaperAccountRow, PaperPositionRow, PaperTradeRow } from "./types";

/**
 * Read queries + server actions for the single-user paper-trading account.
 *
 * Every exported function calls ensurePaperSchema()/ensureSeededAccount()
 * first (both idempotent and cheap), including the read queries, so the
 * account is guaranteed to exist before anyone queries it — per the task
 * spec, not just before writes.
 *
 * Transaction note: the Neon driver's raw client exposes a `sql.transaction()`
 * (see node_modules/@neondatabase/serverless/index.d.ts), but the shared
 * `sql` export in lib/db.ts wraps every call in a plain arrow function that
 * does not forward that method (verified empirically — `sql.transaction` is
 * `undefined` at runtime despite the TS type claiming otherwise), so it is
 * not actually usable here. buyStock/sellStock therefore run sequential
 * awaited statements with no atomicity guarantee across them; a failure
 * partway through (e.g. the trade insert) can leave cash/position updated
 * without a matching trade row.
 */

// Row mappers, following the num()/toIso() coercion convention from
// lib/queries.ts (Postgres numeric/timestamp columns come back as strings /
// Date objects over the Neon HTTP driver, not native JS numbers).

function toIso(value: unknown): string {
  if (value instanceof Date) return value.toISOString();
  return String(value);
}

function mapAccount(row: Record<string, unknown>): PaperAccountRow {
  return {
    id: num(row.id),
    cash_balance: num(row.cash_balance),
    starting_balance: num(row.starting_balance),
    created_at: toIso(row.created_at),
    updated_at: toIso(row.updated_at),
  };
}

function mapPosition(row: Record<string, unknown>): PaperPositionRow {
  return {
    ticker: String(row.ticker),
    quantity: num(row.quantity),
    avg_cost: num(row.avg_cost),
    updated_at: toIso(row.updated_at),
  };
}

function mapTrade(row: Record<string, unknown>): PaperTradeRow {
  return {
    id: num(row.id),
    ticker: String(row.ticker),
    side: row.side === "sell" ? "sell" : "buy",
    quantity: num(row.quantity),
    price: num(row.price),
    realized_pnl: numOrNull(row.realized_pnl),
    executed_at: toIso(row.executed_at),
  };
}

// ---------------------------------------------------------------------------
// Read queries
// ---------------------------------------------------------------------------

/** The single paper_account row. */
export async function getAccount(): Promise<PaperAccountRow> {
  await ensurePaperSchema();
  await ensureSeededAccount();
  const rows = await sql`
    SELECT id, cash_balance, starting_balance, created_at, updated_at
    FROM paper_account
    LIMIT 1
  `;
  return mapAccount(rows[0] as Record<string, unknown>);
}

/** All open positions, ordered by ticker. */
export async function getPositions(): Promise<PaperPositionRow[]> {
  await ensurePaperSchema();
  await ensureSeededAccount();
  const rows = await sql`
    SELECT ticker, quantity, avg_cost, updated_at
    FROM paper_positions
    ORDER BY ticker
  `;
  return rows.map((r) => mapPosition(r as Record<string, unknown>));
}

/** Most recent trades, newest first. */
export async function getTrades(limit = 50): Promise<PaperTradeRow[]> {
  await ensurePaperSchema();
  await ensureSeededAccount();
  const rows = await sql`
    SELECT id, ticker, side, quantity, price, realized_pnl, executed_at
    FROM paper_trades
    ORDER BY executed_at DESC
    LIMIT ${limit}
  `;
  return rows.map((r) => mapTrade(r as Record<string, unknown>));
}

// ---------------------------------------------------------------------------
// Server actions
// ---------------------------------------------------------------------------

export async function buyStock(
  ticker: string,
  quantity: number,
): Promise<{ success: boolean; error?: string }> {
  await ensurePaperSchema();
  await ensureSeededAccount();

  if (!ticker || ticker.trim().length === 0) {
    return { success: false, error: "Ticker is required" };
  }
  if (!(quantity > 0)) {
    return { success: false, error: "Quantity must be greater than zero" };
  }
  const normalizedTicker = ticker.trim().toUpperCase();

  const quotes = await getQuotes([normalizedTicker]);
  const quote = quotes.find((q) => q.ticker === normalizedTicker);
  if (!quote) {
    return { success: false, error: `No quote available for ${normalizedTicker}` };
  }
  const price = quote.price;
  const cost = quantity * price;

  const account = await getAccount();
  if (cost > account.cash_balance) {
    return { success: false, error: "Insufficient funds" };
  }

  const positionRows = await sql`
    SELECT ticker, quantity, avg_cost, updated_at
    FROM paper_positions
    WHERE ticker = ${normalizedTicker}
    LIMIT 1
  `;
  const existing = positionRows.length > 0 ? mapPosition(positionRows[0] as Record<string, unknown>) : null;

  await sql`
    UPDATE paper_account
    SET cash_balance = cash_balance - ${cost}, updated_at = now()
    WHERE id = ${account.id}
  `;

  if (existing) {
    const newQuantity = existing.quantity + quantity;
    const newAvgCost = (existing.quantity * existing.avg_cost + quantity * price) / newQuantity;
    await sql`
      UPDATE paper_positions
      SET quantity = ${newQuantity}, avg_cost = ${newAvgCost}, updated_at = now()
      WHERE ticker = ${normalizedTicker}
    `;
  } else {
    await sql`
      INSERT INTO paper_positions (ticker, quantity, avg_cost, updated_at)
      VALUES (${normalizedTicker}, ${quantity}, ${price}, now())
    `;
  }

  await sql`
    INSERT INTO paper_trades (ticker, side, quantity, price, realized_pnl, executed_at)
    VALUES (${normalizedTicker}, 'buy', ${quantity}, ${price}, NULL, now())
  `;

  revalidatePath("/");
  return { success: true };
}

export async function sellStock(
  ticker: string,
  quantity: number,
): Promise<{ success: boolean; error?: string }> {
  await ensurePaperSchema();
  await ensureSeededAccount();

  if (!ticker || ticker.trim().length === 0) {
    return { success: false, error: "Ticker is required" };
  }
  if (!(quantity > 0)) {
    return { success: false, error: "Quantity must be greater than zero" };
  }
  const normalizedTicker = ticker.trim().toUpperCase();

  const positionRows = await sql`
    SELECT ticker, quantity, avg_cost, updated_at
    FROM paper_positions
    WHERE ticker = ${normalizedTicker}
    LIMIT 1
  `;
  if (positionRows.length === 0) {
    return { success: false, error: `No open position in ${normalizedTicker}` };
  }
  const position = mapPosition(positionRows[0] as Record<string, unknown>);
  if (quantity > position.quantity) {
    return { success: false, error: "Cannot sell more shares than currently held" };
  }

  const quotes = await getQuotes([normalizedTicker]);
  const quote = quotes.find((q) => q.ticker === normalizedTicker);
  if (!quote) {
    return { success: false, error: `No quote available for ${normalizedTicker}` };
  }
  const price = quote.price;
  const proceeds = quantity * price;
  const realizedPnl = (price - position.avg_cost) * quantity;

  const account = await getAccount();
  await sql`
    UPDATE paper_account
    SET cash_balance = cash_balance + ${proceeds}, updated_at = now()
    WHERE id = ${account.id}
  `;

  // Epsilon chosen to be far below the smallest realistic fractional-share
  // quantity while comfortably above floating-point noise, so a sell that's
  // intended to close the position out (e.g. sell exactly `position.quantity`)
  // reliably deletes the row instead of leaving a dust remainder.
  const EPSILON = 1e-9;
  const remaining = position.quantity - quantity;
  if (Math.abs(remaining) < EPSILON) {
    await sql`
      DELETE FROM paper_positions
      WHERE ticker = ${normalizedTicker}
    `;
  } else {
    await sql`
      UPDATE paper_positions
      SET quantity = ${remaining}, updated_at = now()
      WHERE ticker = ${normalizedTicker}
    `;
  }

  await sql`
    INSERT INTO paper_trades (ticker, side, quantity, price, realized_pnl, executed_at)
    VALUES (${normalizedTicker}, 'sell', ${quantity}, ${price}, ${realizedPnl}, now())
  `;

  revalidatePath("/");
  return { success: true };
}
