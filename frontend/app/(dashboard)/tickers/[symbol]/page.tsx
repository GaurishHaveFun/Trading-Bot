import Link from "next/link";
import PriceChart from "@/components/PriceChart";
import PriceStats from "@/components/PriceStats";
import RefreshButton from "@/components/RefreshButton";
import ScoreGauge from "@/components/ScoreGauge";
import { getTickerHistory } from "@/lib/queries";
import type { TickerHistory } from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadHistory(
  ticker: string
): Promise<{ history: TickerHistory | null; error: string | null }> {
  try {
    const history = await getTickerHistory(ticker);
    return { history, error: null };
  } catch (err) {
    console.error("Failed to load ticker history", err);
    return {
      history: null,
      error:
        "Could not load history for this ticker. Check that DATABASE_URL is configured and reachable.",
    };
  }
}

export default async function TickerPage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol } = await params;
  const ticker = symbol.toUpperCase();
  const { history, error } = await loadHistory(ticker);

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="font-display text-2xl font-semibold text-foreground">{ticker}</h1>
        <p className="text-sm text-foreground-muted">Score history and price chart.</p>
      </div>

      {error && (
        <div className="glass-panel px-4 py-3 text-sm" style={{ color: "var(--loss)" }}>
          {error}
        </div>
      )}

      {!error && history && (
        <>
          <div className="glass-panel panel-enter p-4">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-sm font-medium text-foreground-muted">Price Chart</h2>
              <RefreshButton />
            </div>
            <PriceChart bars={history.bars} />
          </div>

          <PriceStats bars={history.bars} latestSnapshot={history.signals[0]?.signal.snapshot} />

          <div className="glass-panel-dense panel-enter overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="border-b border-white/10">
                <tr>
                  <th className="px-4 py-3 text-left font-medium text-foreground-muted">
                    Run timestamp
                  </th>
                  <th className="px-4 py-3 text-left font-medium text-foreground-muted">
                    Score
                  </th>
                  <th className="px-4 py-3 text-right font-medium text-foreground-muted">
                    Rules
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/10">
                {history.signals.length === 0 && (
                  <tr>
                    <td colSpan={3} className="px-4 py-6 text-center text-foreground-muted">
                      No signal history for {ticker}.
                    </td>
                  </tr>
                )}
                {history.signals.map(({ signal, run }) => (
                  <tr key={signal.id} className="glass-row">
                    <td className="px-4 py-3">
                      <Link
                        href={`/runs/${encodeURIComponent(run.run_timestamp)}`}
                        className="font-mono text-foreground hover:text-gradient-accent hover:underline"
                      >
                        {run.run_timestamp}
                      </Link>
                    </td>
                    <td className="px-4 py-3">
                      <ScoreGauge
                        score={signal.score}
                        threshold={run.alert_threshold ?? 0.7}
                        size="sm"
                      />
                    </td>
                    <td className="px-4 py-3 text-right font-mono tabular-nums text-foreground-muted">
                      {signal.rules_passed ?? "—"}/{signal.rules_total ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
