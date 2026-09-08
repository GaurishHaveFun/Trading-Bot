import type { SignalSnapshot } from "./types";

/**
 * Pure analytical core of the Patterns page: given the latest snapshot for
 * every ticker in the universe and the set of tickers the user has flagged
 * as "I'd buy this" (model picks), work out which screener features
 * actually distinguish the picks from the rest.
 *
 * Deliberately has no DB/server imports so it's trivially unit-testable and
 * reusable — all data fetching happens in the caller (the /patterns page).
 */

export interface UniverseEntry {
  ticker: string;
  snapshot: SignalSnapshot;
}

export interface FeatureComparison {
  key: string;
  label: string;
  meanPicked: number;
  meanRest: number;
  /** Point-biserial correlation between this feature and the picked/rest label. */
  correlation: number;
  /** max(|value|) across every universe ticker with a value for this feature — used for bar-width scaling, floored above zero. */
  maxAbsValue: number;
  /** How many universe tickers had a usable (non-null/undefined) value for this feature. */
  sampleSize: number;
}

export interface IndustryBreakdownRow {
  industry: string;
  pickedCount: number;
  restCount: number;
}

export interface PatternsResult {
  minPicksMet: boolean;
  pickedCount: number;
  universeSize: number;
  features: FeatureComparison[];
  industries: IndustryBreakdownRow[];
}

/**
 * Standard Pearson correlation coefficient. Point-biserial correlation
 * (used here to correlate a numeric feature against the binary
 * picked/not-picked label) is mathematically identical to Pearson's r when
 * one variable is 0/1 — no separate formula is needed.
 */
export function pearsonCorrelation(xs: number[], ys: number[]): number {
  const n = xs.length;
  if (n < 2 || ys.length !== n) return 0;

  const meanX = xs.reduce((a, b) => a + b, 0) / n;
  const meanY = ys.reduce((a, b) => a + b, 0) / n;

  let cov = 0;
  let varX = 0;
  let varY = 0;
  for (let i = 0; i < n; i++) {
    const dx = xs[i] - meanX;
    const dy = ys[i] - meanY;
    cov += dx * dy;
    varX += dx * dx;
    varY += dy * dy;
  }

  if (varX === 0 || varY === 0) return 0;
  return cov / Math.sqrt(varX * varY);
}

const NUMERIC_FEATURES: { key: string; label: string; boolean?: boolean }[] = [
  { key: "rsi_14", label: "RSI (14)" },
  { key: "sma_50", label: "SMA 50" },
  { key: "sma_200", label: "SMA 200" },
  { key: "atr_14", label: "ATR (14)" },
  { key: "price_to_book", label: "Price / Book" },
  { key: "change_pct", label: "Change %" },
  { key: "in_watchlist", label: "In Watchlist", boolean: true },
  { key: "is_chip", label: "Is Chip Stock", boolean: true },
];

function featureValue(
  snapshot: SignalSnapshot,
  key: string,
  isBoolean: boolean | undefined,
): number | undefined {
  const raw = snapshot[key];
  if (isBoolean) {
    if (typeof raw !== "boolean") return undefined;
    return raw ? 1 : 0;
  }
  if (typeof raw !== "number" || Number.isNaN(raw)) return undefined;
  return raw;
}

export function buildPatternsResult(
  universe: UniverseEntry[],
  pickedTickers: Set<string>,
): PatternsResult {
  const features: FeatureComparison[] = NUMERIC_FEATURES.map(({ key, label, boolean }) => {
    const picked: number[] = [];
    const rest: number[] = [];
    const allValues: number[] = [];
    const allLabels: number[] = [];

    for (const entry of universe) {
      const value = featureValue(entry.snapshot, key, boolean);
      if (value === undefined) continue;

      const isPicked = pickedTickers.has(entry.ticker);
      (isPicked ? picked : rest).push(value);
      allValues.push(value);
      allLabels.push(isPicked ? 1 : 0);
    }

    const meanPicked = picked.length > 0 ? picked.reduce((a, b) => a + b, 0) / picked.length : 0;
    const meanRest = rest.length > 0 ? rest.reduce((a, b) => a + b, 0) / rest.length : 0;
    const correlation = pearsonCorrelation(allValues, allLabels);
    // Epsilon floor avoids a divide-by-zero in the UI's bar-width calc when
    // every value for a feature happens to be exactly zero.
    const maxAbsValue = Math.max(...allValues.map(Math.abs), 1e-9);

    return {
      key,
      label,
      meanPicked,
      meanRest,
      correlation,
      maxAbsValue,
      sampleSize: allValues.length,
    };
  }).sort((a, b) => Math.abs(b.correlation) - Math.abs(a.correlation));

  const industryCounts = new Map<string, { pickedCount: number; restCount: number }>();
  for (const entry of universe) {
    const industry = entry.snapshot.industry;
    if (!industry) continue;
    const isPicked = pickedTickers.has(entry.ticker);
    const counts = industryCounts.get(industry) ?? { pickedCount: 0, restCount: 0 };
    if (isPicked) counts.pickedCount += 1;
    else counts.restCount += 1;
    industryCounts.set(industry, counts);
  }

  const industries: IndustryBreakdownRow[] = Array.from(industryCounts.entries())
    .map(([industry, counts]) => ({ industry, ...counts }))
    .sort((a, b) => b.pickedCount - a.pickedCount || b.restCount - a.restCount);

  return {
    minPicksMet: pickedTickers.size >= 3,
    pickedCount: pickedTickers.size,
    universeSize: universe.length,
    features,
    industries,
  };
}
