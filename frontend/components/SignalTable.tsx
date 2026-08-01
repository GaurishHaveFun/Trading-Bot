"use client";

import { Fragment, useState } from "react";
import Link from "next/link";
import RuleBreakdown from "./RuleBreakdown";
import ScoreGauge from "./ScoreGauge";
import type { SignalWithRules } from "@/lib/types";

function formatNum(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

/**
 * Ranked signal table shared by the latest-run and specific-run pages.
 * Rows expand (click) to show the per-rule breakdown, and rows scoring at
 * or above the run's alert_threshold are visually highlighted.
 */
export default function SignalTable({
  signals,
  alertThreshold,
}: {
  signals: SignalWithRules[];
  alertThreshold: number | null;
}) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  function toggle(id: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  if (signals.length === 0) {
    return <p className="text-sm text-foreground-muted">No signals in this run.</p>;
  }

  const threshold = alertThreshold ?? 0.7;

  return (
    <div className="glass-panel-dense panel-enter overflow-x-auto">
      <table className="min-w-full text-sm">
        <thead className="border-b border-white/10">
          <tr>
            <th className="px-4 py-3 text-left font-medium text-foreground-muted">Ticker</th>
            <th className="px-4 py-3 text-left font-medium text-foreground-muted">Score</th>
            <th className="px-4 py-3 text-right font-medium text-foreground-muted">Rules</th>
            <th className="px-4 py-3 text-right font-medium text-foreground-muted">Close</th>
            <th className="px-4 py-3 text-right font-medium text-foreground-muted">Change %</th>
            <th className="px-4 py-3 text-right font-medium text-foreground-muted">RSI 14</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/10">
          {signals.map((s) => {
            const passesThreshold =
              alertThreshold !== null && alertThreshold !== undefined && s.score >= alertThreshold;
            const isExpanded = expanded.has(s.id);
            const changePct = s.snapshot?.change_pct;

            return (
              <Fragment key={s.id}>
                <tr
                  onClick={() => toggle(s.id)}
                  className={`glass-row cursor-pointer ${
                    passesThreshold ? "bg-gradient-to-r from-cyan-400/10 to-violet-400/10" : ""
                  }`}
                >
                  <td className="px-4 py-3 font-medium">
                    <Link
                      href={`/screener/tickers/${s.ticker}`}
                      className="font-mono text-foreground hover:text-gradient-accent hover:underline"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {s.ticker}
                    </Link>
                    {passesThreshold && (
                      <span className="ml-2 rounded-md bg-gradient-accent px-1.5 py-0.5 text-[10px] font-semibold uppercase text-[#0a0b14]">
                        Alert
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <ScoreGauge score={s.score} threshold={threshold} size="sm" />
                  </td>
                  <td className="px-4 py-3 text-right font-mono tabular-nums text-foreground-muted">
                    {s.rules_passed ?? "—"}/{s.rules_total ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-right font-mono tabular-nums">
                    {formatNum(s.snapshot?.close)}
                  </td>
                  <td
                    className="px-4 py-3 text-right font-mono tabular-nums"
                    style={{
                      color:
                        typeof changePct === "number" && changePct < 0
                          ? "var(--loss)"
                          : "var(--gain)",
                    }}
                  >
                    {formatNum(changePct)}%
                  </td>
                  <td className="px-4 py-3 text-right font-mono tabular-nums text-foreground-muted">
                    {formatNum(s.snapshot?.rsi_14, 1)}
                  </td>
                </tr>
                {isExpanded && (
                  <tr className="bg-black/20">
                    <td colSpan={6} className="px-4 py-4">
                      <RuleBreakdown ruleResults={s.rule_results} />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
