"use server";

import { revalidatePath } from "next/cache";

/**
 * Server action that triggers a fresh AI analysis generation
 * (POST /tickers/{symbol}/analysis) on the screener API. This is a real LLM
 * call on the backend — 15-40s typically, up to ~120s counting a Render
 * free-tier cold start — so the timeout here is generous.
 *
 * Mirrors lib/model.ts's shape: validates input, never throws, and always
 * resolves to `{ success, error? }` so a client component driving this via
 * useTransition can await it directly without a try/catch at the call site.
 */

const BASE_URL = process.env.SCREENER_API_URL || "http://localhost:8000";

export async function generateAnalysis(
  ticker: string,
): Promise<{ success: boolean; error?: string }> {
  if (!ticker || ticker.trim().length === 0) {
    return { success: false, error: "Ticker is required" };
  }
  const normalizedTicker = ticker.trim().toUpperCase();

  try {
    const url = `${BASE_URL}/tickers/${encodeURIComponent(normalizedTicker)}/analysis`;
    const res = await fetch(url, {
      method: "POST",
      signal: AbortSignal.timeout(120_000),
    });

    if (res.ok) {
      revalidatePath(`/screener/tickers/${normalizedTicker}`);
      return { success: true };
    }

    if (res.status === 503) {
      return { success: false, error: "AI analysis isn't configured on the server yet." };
    }

    return {
      success: false,
      error: `Failed to generate analysis (${res.status}), please try again.`,
    };
  } catch (err) {
    const isTimeout = err instanceof Error && err.name === "TimeoutError";
    console.error("generateAnalysis: failed to reach screener API", err);
    return {
      success: false,
      error: isTimeout
        ? "Analysis timed out, please try again."
        : "Failed to generate analysis, please try again.",
    };
  }
}
