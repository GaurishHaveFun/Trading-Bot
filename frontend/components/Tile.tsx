import type { ReactNode } from "react";

/**
 * Small stat card used across PriceStats, KeyStats, and AnalystTargets.
 * Extracted from PriceStats.tsx (identical signature) since three consumers
 * now share it.
 */
export default function Tile({
  label,
  value,
  tone,
  valueClassName,
}: {
  label: string;
  value: ReactNode;
  tone?: "pos" | "neg" | "neutral";
  valueClassName?: string;
}) {
  const color =
    tone === "pos" ? "var(--gain)" : tone === "neg" ? "var(--loss)" : "var(--foreground)";

  return (
    <div className="glass-panel-dense p-4">
      <div className="text-xs uppercase tracking-wide text-foreground-muted">{label}</div>
      <div
        className={`mt-1 font-mono text-lg font-semibold ${valueClassName ?? ""}`}
        style={{ color }}
      >
        {value}
      </div>
    </div>
  );
}
