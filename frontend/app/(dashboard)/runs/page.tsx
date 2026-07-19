import Link from "next/link";
import { listRuns } from "@/lib/queries";
import type { RunListItem } from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadRuns(): Promise<{ runs: RunListItem[]; error: string | null }> {
  try {
    const runs = await listRuns();
    return { runs, error: null };
  } catch (err) {
    console.error("Failed to load runs", err);
    return {
      runs: [],
      error:
        "Could not load run history. Check that DATABASE_URL is configured and reachable.",
    };
  }
}

export default async function RunsPage() {
  const { runs, error } = await loadRuns();

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="font-display text-2xl font-semibold text-foreground">Runs</h1>
        <p className="text-sm text-foreground-muted">Past screener runs.</p>
      </div>

      {error && (
        <div className="glass-panel px-4 py-3 text-sm" style={{ color: "var(--loss)" }}>
          {error}
        </div>
      )}

      {!error && runs.length === 0 && (
        <div className="glass-panel px-4 py-6 text-center text-sm text-foreground-muted">
          No runs recorded yet.
        </div>
      )}

      {runs.length > 0 && (
        <div className="glass-panel-dense panel-enter overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="border-b border-white/10">
              <tr>
                <th className="px-4 py-3 text-left font-medium text-foreground-muted">
                  Run timestamp
                </th>
                <th className="px-4 py-3 text-left font-medium text-foreground-muted">
                  Universe
                </th>
                <th className="px-4 py-3 text-right font-medium text-foreground-muted">
                  Signals
                </th>
                <th className="px-4 py-3 text-right font-medium text-foreground-muted">
                  Above threshold
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/10">
              {runs.map((run) => (
                <tr key={run.id} className="glass-row">
                  <td className="px-4 py-3">
                    <Link
                      href={`/runs/${encodeURIComponent(run.run_timestamp)}`}
                      className="font-mono font-medium text-foreground hover:text-gradient-accent hover:underline"
                    >
                      {run.run_timestamp}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-foreground-muted">{run.universe ?? "—"}</td>
                  <td className="px-4 py-3 text-right font-mono tabular-nums">
                    {run.signal_count ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-right font-mono tabular-nums text-accent">
                    {run.above_threshold_count}
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
