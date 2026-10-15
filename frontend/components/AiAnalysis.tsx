"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { generateAnalysis } from "@/lib/analysis";
import type { TickerAnalysisResponse } from "@/lib/types";

const CONFIDENCE_COLOR: Record<string, string> = {
  high: "var(--gain)",
  medium: "var(--foreground-muted)",
  low: "var(--loss)",
};

/** "3m ago" / "2h ago" / "5d ago" from an ISO timestamp, computed on render. */
function relativeTime(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const minutes = Math.max(0, Math.round(diffMs / 60_000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

/**
 * AI analysis panel for a ticker detail page. Reads GET
 * /tickers/{symbol}/analysis (cheap, no LLM call) via the `initial` prop
 * from the parent Server Component, and drives a fresh POST generation
 * through the generateAnalysis server action.
 *
 * State ownership: the ready/none analysis content is derived directly from
 * `initial`, not copied into local state — generateAnalysis already calls
 * revalidatePath server-side, and router.refresh() (mirroring
 * RefreshButton.tsx) makes the parent Server Component re-fetch and pass
 * fresh `initial` props down. Local state only tracks the error string, so
 * an error never blanks whatever analysis is already being shown.
 */
export default function AiAnalysis({
  ticker,
  initial,
  changePct,
}: {
  ticker: string;
  initial: TickerAnalysisResponse | null;
  changePct?: number | null;
}) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function handleGenerate() {
    setError(null);
    startTransition(async () => {
      const result = await generateAnalysis(ticker);
      if (result.success) {
        router.refresh();
      } else {
        setError(result.error ?? "Something went wrong");
      }
    });
  }

  const analysis = initial?.analysis ?? null;
  const isReady = initial?.status === "ready" && analysis !== null;
  const confidenceColor = analysis ? CONFIDENCE_COLOR[analysis.confidence] : undefined;
  const droppy = typeof changePct === "number" && changePct < 0;

  return (
    <div className="glass-panel panel-enter p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-medium text-foreground-muted">AI analysis</h2>
        {isReady && (
          <button
            type="button"
            onClick={handleGenerate}
            disabled={isPending}
            aria-busy={isPending}
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-xs font-medium text-foreground-muted transition-colors hover:border-white/20 hover:bg-white/10 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isPending ? "Refreshing…" : "Refresh"}
          </button>
        )}
      </div>

      {isPending && (
        <p className="mb-3 text-xs text-foreground-muted">
          Generating analysis… this can take up to a minute, the backend may be cold-starting.
        </p>
      )}

      {isReady && analysis && (
        <div className="flex flex-col gap-3">
          <div className="flex items-start justify-between gap-3">
            <p className="text-sm font-medium text-foreground">{analysis.headline}</p>
            <span
              className="shrink-0 rounded-md px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
              style={{
                color: confidenceColor ?? "var(--foreground)",
                backgroundColor: "rgba(255, 255, 255, 0.08)",
              }}
            >
              {analysis.confidence} confidence
            </span>
          </div>

          {analysis.drivers.length > 0 && (
            <div className="flex flex-col gap-2">
              {analysis.drivers.map((driver, i) => (
                <div key={i} className="rounded-lg border border-white/10 bg-white/5 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-xs font-medium text-foreground">{driver.title}</p>
                    <span className="shrink-0 text-[10px] uppercase tracking-wide text-foreground-muted">
                      {driver.evidence_type}
                    </span>
                  </div>
                  <p className="mt-1 text-xs leading-relaxed text-foreground-muted">
                    {driver.explanation}
                  </p>
                </div>
              ))}
            </div>
          )}

          {analysis.sector_context && (
            <p className="text-xs text-foreground-muted">{analysis.sector_context}</p>
          )}

          {analysis.what_to_watch.length > 0 && (
            <div>
              <p className="text-xs font-medium text-foreground-muted">What to watch</p>
              <ul className="mt-1 list-inside list-disc text-xs leading-relaxed text-foreground-muted">
                {analysis.what_to_watch.map((item, i) => (
                  <li key={i}>{item}</li>
                ))}
              </ul>
            </div>
          )}

          {analysis.sources.length > 0 && (
            <div>
              <p className="text-xs font-medium text-foreground-muted">Sources</p>
              <ul className="mt-1 flex flex-col gap-0.5 text-xs">
                {analysis.sources.map((source, i) => (
                  <li key={i}>
                    <a
                      href={source.url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-gradient-accent hover:underline"
                    >
                      {source.title}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {initial?.generated_at && (
            <p className="text-xs text-foreground-muted">
              Generated {relativeTime(initial.generated_at)}
            </p>
          )}
        </div>
      )}

      {!isReady && !isPending && (
        <div className="flex flex-col gap-2">
          <p className="text-xs text-foreground-muted">
            {droppy
              ? "No AI analysis yet for this drop."
              : "No AI analysis yet for this ticker."}
          </p>
          <button
            type="button"
            onClick={handleGenerate}
            disabled={isPending}
            aria-busy={isPending}
            className="inline-flex w-fit items-center gap-1.5 rounded-lg bg-gradient-accent px-3 py-1.5 text-xs font-semibold text-[#0a0b14] transition-colors hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isPending ? "…" : "Analyze this drop"}
          </button>
        </div>
      )}

      {error && (
        <p className="mt-3 text-xs" style={{ color: "var(--loss)" }} role="alert">
          {error}
        </p>
      )}

      <p className="mt-3 text-xs text-foreground-muted">
        AI-generated analysis. May be inaccurate. Not financial advice.
      </p>
    </div>
  );
}
