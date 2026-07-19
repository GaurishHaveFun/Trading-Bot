import { notFound } from "next/navigation";
import SignalTable from "@/components/SignalTable";
import { getRun } from "@/lib/queries";
import type { RunWithSignals } from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadRun(
  timestamp: string
): Promise<{ run: RunWithSignals | null; error: string | null }> {
  try {
    const run = await getRun(timestamp);
    return { run, error: null };
  } catch (err) {
    console.error("Failed to load run", err);
    return {
      run: null,
      error:
        "Could not load this run. Check that DATABASE_URL is configured and reachable.",
    };
  }
}

export default async function RunDetailPage({
  params,
}: {
  params: Promise<{ timestamp: string }>;
}) {
  const { timestamp } = await params;
  const { run, error } = await loadRun(timestamp);

  if (error) {
    return (
      <div className="glass-panel px-4 py-3 text-sm" style={{ color: "var(--loss)" }}>
        {error}
      </div>
    );
  }

  if (!run) {
    notFound();
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="font-display text-2xl font-semibold text-foreground">
          {run.run_timestamp}
        </h1>
        <p className="text-sm text-foreground-muted">
          Universe: {run.universe ?? "—"} · Alert threshold:{" "}
          <span className="font-mono">{run.alert_threshold ?? "—"}</span>
        </p>
      </div>

      <SignalTable signals={run.signals} alertThreshold={run.alert_threshold} />
    </div>
  );
}
