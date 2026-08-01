import type { PaperTradeRow } from "@/lib/types";

function formatCurrency(value: number): string {
  return value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  });
}

/** Recent paper-trade fills, newest first. */
export default function TradesList({ trades }: { trades: PaperTradeRow[] }) {
  if (trades.length === 0) {
    return (
      <div className="glass-panel px-4 py-6 text-center text-sm text-foreground-muted">
        No trades yet.
      </div>
    );
  }

  return (
    <div className="glass-panel-dense panel-enter overflow-x-auto">
      <table className="min-w-full text-sm">
        <thead className="border-b border-white/10">
          <tr>
            <th className="px-4 py-3 text-left font-medium text-foreground-muted">Ticker</th>
            <th className="px-4 py-3 text-left font-medium text-foreground-muted">Side</th>
            <th className="px-4 py-3 text-right font-medium text-foreground-muted">Qty</th>
            <th className="px-4 py-3 text-right font-medium text-foreground-muted">Price</th>
            <th className="px-4 py-3 text-right font-medium text-foreground-muted">Realized P&L</th>
            <th className="px-4 py-3 text-left font-medium text-foreground-muted">Time</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/10">
          {trades.map((t) => (
            <tr key={t.id} className="glass-row">
              <td className="px-4 py-3 font-mono font-medium text-foreground">{t.ticker}</td>
              <td
                className="px-4 py-3 text-xs font-semibold uppercase tracking-wide"
                style={{ color: t.side === "buy" ? "var(--gain)" : "var(--loss)" }}
              >
                {t.side}
              </td>
              <td className="px-4 py-3 text-right font-mono tabular-nums">{t.quantity}</td>
              <td className="px-4 py-3 text-right font-mono tabular-nums">
                {formatCurrency(t.price)}
              </td>
              <td
                className="px-4 py-3 text-right font-mono tabular-nums"
                style={{
                  color:
                    t.realized_pnl === null
                      ? undefined
                      : t.realized_pnl >= 0
                        ? "var(--gain)"
                        : "var(--loss)",
                }}
              >
                {t.realized_pnl !== null
                  ? `${t.realized_pnl >= 0 ? "+" : ""}${formatCurrency(t.realized_pnl)}`
                  : "—"}
              </td>
              <td className="px-4 py-3 font-mono text-foreground-muted">{t.executed_at}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
