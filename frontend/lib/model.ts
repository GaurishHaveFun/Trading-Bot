"use server";

import { revalidatePath } from "next/cache";
import { sql } from "./db";
import { ensureModelSchema } from "./model-schema";
import type { ModelPickRow } from "./types";

/**
 * Read queries + server actions for the single-user "model picks" list —
 * tickers the user has flagged as ones they'd actually buy from the ticker
 * detail page. Mirrors lib/paper.ts's structure: every exported function
 * calls ensureModelSchema() first, and mutating actions return
 * `{ success, error? }` instead of throwing.
 */

function toIso(value: unknown): string {
  if (value instanceof Date) return value.toISOString();
  return String(value);
}

function mapModelPick(row: Record<string, unknown>): ModelPickRow {
  return {
    ticker: String(row.ticker),
    added_at: toIso(row.added_at),
  };
}

/** All model picks, most recently added first. */
export async function getModelPicks(): Promise<ModelPickRow[]> {
  await ensureModelSchema();
  const rows = await sql`
    SELECT ticker, added_at FROM model_picks ORDER BY added_at DESC
  `;
  return rows.map((r) => mapModelPick(r as Record<string, unknown>));
}

/** Whether a given ticker is currently flagged as a model pick. */
export async function isTickerInModel(ticker: string): Promise<boolean> {
  await ensureModelSchema();
  const normalized = ticker.trim().toUpperCase();
  const rows = await sql`
    SELECT 1 FROM model_picks WHERE ticker = ${normalized} LIMIT 1
  `;
  return rows.length > 0;
}

export async function addToModel(
  ticker: string,
): Promise<{ success: boolean; error?: string }> {
  if (!ticker || ticker.trim().length === 0) {
    return { success: false, error: "Ticker is required" };
  }
  const normalizedTicker = ticker.trim().toUpperCase();

  await ensureModelSchema();
  await sql`
    INSERT INTO model_picks (ticker) VALUES (${normalizedTicker})
    ON CONFLICT (ticker) DO NOTHING
  `;

  revalidatePath(`/screener/tickers/${normalizedTicker}`);
  revalidatePath("/patterns");
  return { success: true };
}

export async function removeFromModel(
  ticker: string,
): Promise<{ success: boolean; error?: string }> {
  if (!ticker || ticker.trim().length === 0) {
    return { success: false, error: "Ticker is required" };
  }
  const normalizedTicker = ticker.trim().toUpperCase();

  await ensureModelSchema();
  await sql`
    DELETE FROM model_picks WHERE ticker = ${normalizedTicker}
  `;

  revalidatePath(`/screener/tickers/${normalizedTicker}`);
  revalidatePath("/patterns");
  return { success: true };
}
