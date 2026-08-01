function formatCurrency(value: number): string {
  return value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  });
}

/** Hero block: total account value plus unrealized P&L and cash on hand. */
export default function PortfolioSummary({
  totalValue,
  cashBalance,
  unrealizedPnl,
  unrealizedPnlPct,
}: {
  totalValue: number;
  cashBalance: number;
  unrealizedPnl: number;
  unrealizedPnlPct: number;
}) {
  const pnlColor = unrealizedPnl >= 0 ? "var(--gain)" : "var(--loss)";
  const sign = unrealizedPnl >= 0 ? "+" : "";

  return (
    <section className="glass-panel-dense panel-enter p-6 sm:p-8">
      <p className="font-mono text-xs uppercase tracking-widest text-gradient-accent">
        Account value
      </p>
      <p className="mt-2 font-display text-4xl font-bold text-foreground sm:text-6xl">
        {formatCurrency(totalValue)}
      </p>
      <div className="mt-5 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
        <span className="font-mono font-medium" style={{ color: pnlColor }}>
          {sign}
          {formatCurrency(unrealizedPnl)} ({sign}
          {unrealizedPnlPct.toFixed(2)}%) unrealized
        </span>
        <span className="text-foreground-muted">
          Cash <span className="font-mono text-foreground">{formatCurrency(cashBalance)}</span>
        </span>
      </div>
    </section>
  );
}
