"use client";

import { useState } from "react";
import type { RuleResultRow } from "@/lib/types";

/** Map a rule weight to a dot diameter in px — bigger weight, bigger dot. */
function dotSize(weight: number | null): number {
  const w = weight ?? 0;
  const min = 8;
  const max = 20;
  // Weights in this app range roughly 0.5–2.0; clamp and scale.
  const scaled = min + (Math.min(Math.max(w, 0), 2.5) / 2.5) * (max - min);
  return Math.round(scaled);
}

/**
 * Per-signal rule pass/fail list, encoded as a row of dots sized by weight
 * (bigger weight = bigger dot), colored gain/loss for pass/fail. Rule name,
 * weight, and detail stay visible as an expandable row per rule so no
 * information is lost relative to the old text-only version.
 */
export default function RuleBreakdown({
  ruleResults,
}: {
  ruleResults: RuleResultRow[];
}) {
  const [openId, setOpenId] = useState<number | null>(null);

  if (ruleResults.length === 0) {
    return (
      <p className="text-xs text-foreground-muted">
        No rule results recorded for this signal.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        {ruleResults.map((rr) => {
          const size = dotSize(rr.weight);
          return (
            <button
              key={rr.id}
              type="button"
              onClick={() => setOpenId((prev) => (prev === rr.id ? null : rr.id))}
              className="flex items-center gap-1.5 rounded-lg px-1.5 py-1 transition-colors hover:bg-white/8"
              title={`${rr.rule_name} — weight ${rr.weight ?? "—"} — ${rr.passed ? "passed" : "failed"}`}
            >
              <span
                className="inline-block shrink-0 rounded-full"
                style={{
                  width: size,
                  height: size,
                  backgroundColor: rr.passed ? "var(--gain)" : "var(--loss)",
                  boxShadow: rr.passed
                    ? "0 0 6px rgba(52, 211, 153, 0.5)"
                    : "0 0 6px rgba(248, 113, 113, 0.5)",
                }}
                aria-hidden="true"
              />
              <span className="text-xs text-foreground-muted whitespace-nowrap">
                {rr.rule_name}
              </span>
            </button>
          );
        })}
      </div>

      <ul className="flex flex-col gap-2">
        {ruleResults
          .filter((rr) => openId === rr.id)
          .map((rr) => (
            <li
              key={rr.id}
              className="glass-panel flex flex-col gap-1 p-3 text-xs"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium text-foreground">{rr.rule_name}</span>
                <span className="flex items-center gap-2">
                  <span className="font-mono text-foreground-muted">
                    weight {rr.weight ?? "—"}
                  </span>
                  <span
                    className="rounded-md px-1.5 py-0.5 font-semibold uppercase text-[10px]"
                    style={{
                      backgroundColor: rr.passed ? "var(--gain)" : "var(--loss)",
                      color: "#0a0b14",
                    }}
                  >
                    {rr.passed ? "pass" : "fail"}
                  </span>
                </span>
              </div>
              {rr.detail && Object.keys(rr.detail).length > 0 && (
                <pre className="overflow-x-auto rounded-lg bg-black/30 p-2 font-mono text-[11px] text-foreground-muted">
                  {JSON.stringify(rr.detail, null, 2)}
                </pre>
              )}
            </li>
          ))}
      </ul>
    </div>
  );
}
