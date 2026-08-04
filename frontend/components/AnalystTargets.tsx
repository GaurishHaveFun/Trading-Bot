import Tile from "./Tile";
import { formatNum } from "@/lib/format";
import type { TickerTargets } from "@/lib/types";

const RECOMMENDATION_COLOR: Record<string, string> = {
  strong_buy: "var(--gain)",
  buy: "var(--gain)",
  hold: "var(--foreground-muted)",
  sell: "var(--loss)",
  strong_sell: "var(--loss)",
};

/**
 * Analyst price-target panel. Returns null when targets.mean is null —
 * most small caps have no analyst coverage, and an all-dashes panel isn't
 * worth the vertical space.
 */
export default function AnalystTargets({
  targets,
  price,
}: {
  targets: TickerTargets;
  price: number | null;
}) {
  if (targets.mean === null || targets.mean === undefined) return null;

  const upside =
    price !== null && price !== undefined && price !== 0
      ? ((targets.mean - price) / price) * 100
      : null;

  const recKey = targets.recommendation_key ?? null;
  const recColor = recKey ? RECOMMENDATION_COLOR[recKey.toLowerCase()] : undefined;

  return (
    <div className="glass-panel panel-enter p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-medium text-foreground-muted">Analyst targets</h2>
        {recKey && (
          <span
            className="rounded-md px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
            style={{
              color: recColor ?? "var(--foreground)",
              backgroundColor: "rgba(255, 255, 255, 0.08)",
            }}
          >
            {recKey.replace(/_/g, " ")}
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        <Tile label="Low" value={formatNum(targets.low)} />
        <Tile label="Mean" value={formatNum(targets.mean)} valueClassName="text-2xl" />
        <Tile label="High" value={formatNum(targets.high)} />
        <Tile
          label="Implied upside"
          value={upside !== null ? `${upside >= 0 ? "+" : ""}${formatNum(upside)}%` : "—"}
          tone={upside === null ? "neutral" : upside >= 0 ? "pos" : "neg"}
        />
      </div>

      {targets.analyst_count !== null && targets.analyst_count !== undefined && (
        <p className="mt-3 text-xs text-foreground-muted">
          Based on {targets.analyst_count} analyst{targets.analyst_count === 1 ? "" : "s"}.
        </p>
      )}
    </div>
  );
}
