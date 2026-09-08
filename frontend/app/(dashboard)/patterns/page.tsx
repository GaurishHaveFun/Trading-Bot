import Link from "next/link";
import FeatureCompareBar from "@/components/FeatureCompareBar";
import { getModelPicks } from "@/lib/model";
import { buildPatternsResult, type UniverseEntry } from "@/lib/patterns";
import { getLatestSignalSnapshots } from "@/lib/queries";

export const dynamic = "force-dynamic";

const INDUSTRY_DISPLAY_LIMIT = 15;

export default async function PatternsPage() {
  const [picks, snapshots] = await Promise.all([getModelPicks(), getLatestSignalSnapshots()]);

  const pickedTickers = new Set(picks.map((p) => p.ticker));
  const universe: UniverseEntry[] = snapshots.map((s) => ({
    ticker: s.ticker,
    snapshot: s.snapshot,
  }));

  const result = buildPatternsResult(universe, pickedTickers);
  const visibleFeatures = result.features.filter((f) => f.sampleSize > 0);
  const visibleIndustries = result.industries.slice(0, INDUSTRY_DISPLAY_LIMIT);

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="font-display text-2xl font-semibold text-foreground">Patterns</h1>
        <p className="text-sm text-foreground-muted">
          Reverse-engineers what actually distinguishes the tickers you&apos;ve marked
          &quot;I&apos;d buy this&quot; from the rest of the screener universe.
        </p>
      </div>

      {!result.minPicksMet ? (
        <div className="glass-panel panel-enter flex flex-col gap-3 p-4">
          <p className="text-sm text-foreground-muted">
            Add at least 3 picks from ticker pages to see patterns. You have{" "}
            <span className="font-mono text-foreground">{result.pickedCount}</span> so far.
          </p>
          {picks.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {picks.map((p) => (
                <Link
                  key={p.ticker}
                  href={`/screener/tickers/${p.ticker}`}
                  className="inline-block rounded-md bg-gradient-accent px-1.5 py-0.5 text-[10px] font-semibold uppercase text-[#0a0b14] transition-opacity hover:opacity-80"
                >
                  {p.ticker}
                </Link>
              ))}
            </div>
          )}
        </div>
      ) : (
        <>
          <div className="glass-panel panel-enter flex flex-wrap items-center gap-1.5 p-4">
            <span className="mr-1 text-sm text-foreground-muted">
              {result.pickedCount} pick{result.pickedCount === 1 ? "" : "s"}:
            </span>
            {picks.map((p) => (
              <Link
                key={p.ticker}
                href={`/screener/tickers/${p.ticker}`}
                className="inline-block rounded-md bg-gradient-accent px-1.5 py-0.5 text-[10px] font-semibold uppercase text-[#0a0b14] transition-opacity hover:opacity-80"
              >
                {p.ticker}
              </Link>
            ))}
          </div>

          <div className="glass-panel panel-enter p-4">
            <h2 className="mb-2 text-sm font-medium text-foreground-muted">Feature comparison</h2>
            <div className="divide-y divide-white/10">
              {visibleFeatures.map((f) => (
                <FeatureCompareBar
                  key={f.key}
                  label={f.label}
                  meanPicked={f.meanPicked}
                  meanRest={f.meanRest}
                  maxAbsValue={f.maxAbsValue}
                  sampleSize={f.sampleSize}
                />
              ))}
              {visibleFeatures.length === 0 && (
                <p className="py-2 text-sm text-foreground-muted">
                  No usable feature data across the universe yet.
                </p>
              )}
            </div>
          </div>

          <div className="glass-panel-dense panel-enter overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="border-b border-white/10">
                <tr>
                  <th className="px-4 py-3 text-left font-medium text-foreground-muted">
                    Feature
                  </th>
                  <th className="px-4 py-3 text-right font-medium text-foreground-muted">
                    Correlation
                  </th>
                  <th className="px-4 py-3 text-left font-medium text-foreground-muted">
                    Magnitude
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/10">
                {result.features.map((f) => (
                  <tr key={f.key} className="glass-row">
                    <td className="px-4 py-3 text-foreground">{f.label}</td>
                    <td className="px-4 py-3 text-right font-mono tabular-nums text-foreground">
                      {f.correlation >= 0 ? "+" : ""}
                      {f.correlation.toFixed(2)}
                    </td>
                    <td className="px-4 py-3">
                      <div className="h-1.5 w-32 overflow-hidden rounded-full bg-white/5">
                        <div
                          className="h-full rounded-full bg-gradient-accent"
                          style={{ width: `${Math.min(100, Math.abs(f.correlation) * 100)}%` }}
                        />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="glass-panel-dense panel-enter overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="border-b border-white/10">
                <tr>
                  <th className="px-4 py-3 text-left font-medium text-foreground-muted">
                    Industry
                  </th>
                  <th className="px-4 py-3 text-right font-medium text-foreground-muted">
                    Your picks
                  </th>
                  <th className="px-4 py-3 text-right font-medium text-foreground-muted">
                    Rest of universe
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/10">
                {visibleIndustries.map((row) => (
                  <tr key={row.industry} className="glass-row">
                    <td className="px-4 py-3 text-foreground">{row.industry}</td>
                    <td className="px-4 py-3 text-right font-mono tabular-nums text-foreground">
                      {row.pickedCount}
                    </td>
                    <td className="px-4 py-3 text-right font-mono tabular-nums text-foreground-muted">
                      {row.restCount}
                    </td>
                  </tr>
                ))}
                {visibleIndustries.length === 0 && (
                  <tr>
                    <td className="px-4 py-3 text-foreground-muted" colSpan={3}>
                      No industry data available.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
            {result.industries.length > INDUSTRY_DISPLAY_LIMIT && (
              <p className="px-4 py-2 text-xs text-foreground-muted">
                Showing top {INDUSTRY_DISPLAY_LIMIT} of {result.industries.length} industries.
              </p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
