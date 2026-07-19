import Link from "next/link";
import { listBacktests } from "@/lib/queries";
import type { BacktestRow } from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadBacktests(): Promise<{
  backtests: BacktestRow[];
  error: string | null;
}> {
  try {
    const backtests = await listBacktests();
    return { backtests, error: null };
  } catch (err) {
    console.error("Failed to load backtests", err);
    return {
      backtests: [],
      error:
        "Could not load backtests. Check that DATABASE_URL is configured and reachable.",
    };
  }
}

export default async function BacktestsPage() {
  const { backtests, error } = await loadBacktests();

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="font-display text-2xl font-semibold text-foreground">Backtests</h1>
        <p className="text-sm text-foreground-muted">Historical rule backtests.</p>
      </div>

      {error && (
        <div className="glass-panel px-4 py-3 text-sm" style={{ color: "var(--loss)" }}>
          {error}
        </div>
      )}

      {!error && backtests.length === 0 && (
        <div className="glass-panel px-4 py-6 text-center text-sm text-foreground-muted">
          No backtests recorded yet.
        </div>
      )}

      {backtests.length > 0 && (
        <div className="glass-panel-dense panel-enter overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="border-b border-white/10">
              <tr>
                <th className="px-4 py-3 text-left font-medium text-foreground-muted">
                  Created at
                </th>
                <th className="px-4 py-3 text-right font-medium text-foreground-muted">
                  Win rate
                </th>
                <th className="px-4 py-3 text-right font-medium text-foreground-muted">
                  Avg return
                </th>
                <th className="px-4 py-3 text-right font-medium text-foreground-muted">
                  Signals
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/10">
              {backtests.map((bt) => (
                <tr key={bt.id} className="glass-row">
                  <td className="px-4 py-3">
                    <Link
                      href={`/backtests/${bt.id}`}
                      className="font-mono font-medium text-foreground hover:text-gradient-accent hover:underline"
                    >
                      {bt.created_at}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-right font-mono tabular-nums">
                    {bt.win_rate !== null ? `${bt.win_rate.toFixed(1)}%` : "—"}
                  </td>
                  <td
                    className="px-4 py-3 text-right font-mono tabular-nums"
                    style={{
                      color:
                        bt.avg_return_pct !== null
                          ? bt.avg_return_pct >= 0
                            ? "var(--gain)"
                            : "var(--loss)"
                          : undefined,
                    }}
                  >
                    {bt.avg_return_pct !== null ? `${bt.avg_return_pct.toFixed(2)}%` : "—"}
                  </td>
                  <td className="px-4 py-3 text-right font-mono tabular-nums">
                    {bt.total_signals ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
