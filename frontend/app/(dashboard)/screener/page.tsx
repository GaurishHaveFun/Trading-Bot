import Link from "next/link";
import SignalTable from "@/components/SignalTable";
import ScoreGauge from "@/components/ScoreGauge";
import { getLatestRun } from "@/lib/queries";
import type { RunWithSignals } from "@/lib/types";

// There's no useful static shell here (data changes on every screener run,
// and there may be no DB configured at build time) — always render at
// request time.
export const dynamic = "force-dynamic";

async function loadLatestRun(): Promise<{
  run: RunWithSignals | null;
  error: string | null;
}> {
  try {
    const run = await getLatestRun();
    return { run, error: null };
  } catch (err) {
    console.error("Failed to load latest run", err);
    return {
      run: null,
      error:
        "Could not load the latest run. Check that DATABASE_URL is configured and reachable.",
    };
  }
}

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="glass-panel p-3">
      <dt className="text-xs uppercase tracking-wide text-foreground-muted">{label}</dt>
      <dd className="mt-1 font-mono font-medium text-foreground">{value}</dd>
    </div>
  );
}

export default async function LatestRunPage() {
  const { run, error } = await loadLatestRun();

  const threshold = run?.alert_threshold ?? 0.7;
  const top = run?.signals?.[0] ?? null;

  return (
    <div className="flex flex-col gap-6">
      {error && (
        <div
          className="glass-panel px-4 py-3 text-sm"
          style={{ color: "var(--loss)" }}
        >
          {error}
        </div>
      )}

      {!error && !run && (
        <div className="glass-panel px-4 py-6 text-center text-sm text-foreground-muted">
          No runs recorded yet.
        </div>
      )}

      {run && (
        <>
          {top ? (
            <section className="glass-panel-dense panel-enter p-6 sm:p-8">
              <p className="font-mono text-xs uppercase tracking-widest text-gradient-accent">
                What fired today
              </p>
              <div className="mt-2 flex flex-wrap items-baseline gap-4">
                <Link
                  href={`/screener/tickers/${top.ticker}`}
                  className="font-display text-4xl font-bold text-foreground transition-colors hover:text-gradient-accent sm:text-5xl"
                >
                  {top.ticker}
                </Link>
                <span className="font-mono text-sm text-foreground-muted">
                  {top.rules_passed ?? "—"}/{top.rules_total ?? "—"} rules passed
                </span>
              </div>
              <div className="mt-5 max-w-md">
                <ScoreGauge score={top.score} threshold={threshold} size="lg" />
              </div>
              <dl className="mt-6 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
                <StatTile
                  label="Close"
                  value={top.snapshot?.close !== undefined ? top.snapshot.close.toFixed(2) : "—"}
                />
                <StatTile
                  label="Change %"
                  value={
                    top.snapshot?.change_pct !== undefined
                      ? `${top.snapshot.change_pct.toFixed(2)}%`
                      : "—"
                  }
                />
                <StatTile
                  label="RSI 14"
                  value={top.snapshot?.rsi_14 !== undefined ? top.snapshot.rsi_14.toFixed(1) : "—"}
                />
                <StatTile
                  label="Industry"
                  value={top.snapshot?.industry ?? "—"}
                />
              </dl>
            </section>
          ) : (
            <div>
              <h1 className="font-display text-2xl font-semibold text-foreground">
                Latest run
              </h1>
              <p className="text-sm text-foreground-muted">No signals in this run.</p>
            </div>
          )}

          <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
            <StatTile label="Run timestamp" value={run.run_timestamp} />
            <StatTile label="Universe" value={run.universe ?? "—"} />
            <StatTile label="Alert threshold" value={String(run.alert_threshold ?? "—")} />
            <StatTile label="Signals" value={String(run.signals.length)} />
          </dl>

          <SignalTable signals={run.signals} alertThreshold={run.alert_threshold} />
        </>
      )}
    </div>
  );
}
