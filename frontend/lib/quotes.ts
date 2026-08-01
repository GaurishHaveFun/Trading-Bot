import type { LoserRow, QuoteRow } from "./types";

/**
 * Thin client for the FastAPI screener service (backend/src/screener/api/app.py).
 *
 * Base URL comes from SCREENER_API_URL, defaulting to the local dev server.
 * All requests use `cache: "no-store"` (still the correct Next.js fetch
 * option for "always hit the network" — see
 * node_modules/next/dist/docs/01-app/03-api-reference/04-functions/fetch.md)
 * because quote/loser data is live-ish price data that must never be served
 * stale out of Next's fetch cache.
 */

const BASE_URL = process.env.SCREENER_API_URL || "http://localhost:8000";

/**
 * Error handling choice: return [] on any fetch/network/HTTP failure instead
 * of throwing. Callers like buyStock/sellStock in lib/paper.ts need "no
 * quote returned" to be a normal control-flow branch (e.g. "price
 * unavailable, reject the trade"), not a try/catch around every call site.
 */
export async function getQuotes(symbols: string[]): Promise<QuoteRow[]> {
  if (symbols.length === 0) return [];

  try {
    const url = `${BASE_URL}/quotes?symbols=${encodeURIComponent(symbols.join(","))}`;
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) {
      console.error(`getQuotes: screener API returned ${res.status} ${res.statusText}`);
      return [];
    }
    return (await res.json()) as QuoteRow[];
  } catch (err) {
    console.error("getQuotes: failed to reach screener API", err);
    return [];
  }
}

/**
 * Same error-handling rationale as getQuotes: return [] on failure rather
 * than throwing, so callers can treat "no losers available" as a normal,
 * checkable result.
 */
export async function getLosers(limit = 20): Promise<LoserRow[]> {
  try {
    const url = `${BASE_URL}/losers?limit=${limit}`;
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) {
      console.error(`getLosers: screener API returned ${res.status} ${res.statusText}`);
      return [];
    }
    return (await res.json()) as LoserRow[];
  } catch (err) {
    console.error("getLosers: failed to reach screener API", err);
    return [];
  }
}
