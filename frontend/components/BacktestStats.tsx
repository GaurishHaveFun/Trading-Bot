import type { BacktestRow } from "@/lib/types";

function formatPct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value.toFixed(digits)}%`;
}

function Tile({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "pos" | "neg" | "neutral";
}) {
  const color =
    tone === "pos" ? "var(--gain)" : tone === "neg" ? "var(--loss)" : "var(--foreground)";

  return (
    <div className="glass-panel glass-interactive p-4">
      <div className="text-xs uppercase tracking-wide text-foreground-muted">{label}</div>
      <div className="mt-1 font-mono text-2xl font-semibold" style={{ color }}>
        {value}
      </div>
    </div>
  );
}

/** Summary stat tiles for a backtest. */
export default function BacktestStats({ backtest }: { backtest: BacktestRow }) {
  const avgReturnTone = (backtest.avg_return_pct ?? 0) >= 0 ? "pos" : "neg";
  const totalReturnTone = (backtest.total_return_pct ?? 0) >= 0 ? "pos" : "neg";

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
      <Tile label="Win rate" value={formatPct(backtest.win_rate)} />
      <Tile label="Total signals" value={String(backtest.total_signals ?? "—")} />
      <Tile label="Wins / Losses" value={`${backtest.wins ?? "—"} / ${backtest.losses ?? "—"}`} />
      <Tile label="Avg return" value={formatPct(backtest.avg_return_pct)} tone={avgReturnTone} />
      <Tile
        label="Total return"
        value={formatPct(backtest.total_return_pct)}
        tone={totalReturnTone}
      />
      <Tile label="Best trade" value={formatPct(backtest.best_trade_return_pct)} tone="pos" />
      <Tile label="Worst trade" value={formatPct(backtest.worst_trade_return_pct)} tone="neg" />
      <Tile label="Baseline avg return" value={formatPct(backtest.baseline_avg_return_pct)} />
    </div>
  );
}
