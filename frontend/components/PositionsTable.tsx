import Link from "next/link";
import TradeTicket from "./TradeTicket";
import type { PaperPositionRow, QuoteRow } from "@/lib/types";

function formatCurrency(value: number): string {
  return value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  });
}

/**
 * Holdings table. `quotes` is keyed by ticker so a missing entry (live
 * quote fetch failed for that symbol) degrades gracefully to "—" for every
 * price-derived cell instead of NaN.
 */
export default function PositionsTable({
  positions,
  quotes,
}: {
  positions: PaperPositionRow[];
  quotes: Map<string, QuoteRow>;
}) {
  if (positions.length === 0) {
    return (
      <div className="glass-panel px-4 py-6 text-center text-sm text-foreground-muted">
        No open positions. Use the buy panel above to place your first paper trade.
      </div>
    );
  }

  return (
    <div className="glass-panel-dense panel-enter overflow-x-auto">
      <table className="min-w-full text-sm">
        <thead className="border-b border-white/10">
          <tr>
            <th className="px-4 py-3 text-left font-medium text-foreground-muted">Ticker</th>
            <th className="px-4 py-3 text-right font-medium text-foreground-muted">Qty</th>
            <th className="px-4 py-3 text-right font-medium text-foreground-muted">Avg cost</th>
            <th className="px-4 py-3 text-right font-medium text-foreground-muted">Price</th>
            <th className="px-4 py-3 text-right font-medium text-foreground-muted">Mkt value</th>
            <th className="px-4 py-3 text-right font-medium text-foreground-muted">Unrealized P&L</th>
            <th className="px-4 py-3 text-right font-medium text-foreground-muted">Day %</th>
            <th className="px-4 py-3 text-right font-medium text-foreground-muted">Action</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/10">
          {positions.map((p) => {
            const quote = quotes.get(p.ticker);
            const price = quote?.price;
            const marketValue = price != null ? price * p.quantity : null;
            const unrealizedPnl = price != null ? (price - p.avg_cost) * p.quantity : null;
            const unrealizedPnlPct =
              price != null && p.avg_cost > 0
                ? ((price - p.avg_cost) / p.avg_cost) * 100
                : null;
            const changePct = quote?.change_pct ?? null;

            return (
              <tr key={p.ticker} className="glass-row">
                <td className="px-4 py-3 font-medium">
                  <Link
                    href={`/screener/tickers/${p.ticker}`}
                    className="font-mono text-foreground hover:text-gradient-accent hover:underline"
                  >
                    {p.ticker}
                  </Link>
                </td>
                <td className="px-4 py-3 text-right font-mono tabular-nums">{p.quantity}</td>
                <td className="px-4 py-3 text-right font-mono tabular-nums">
                  {formatCurrency(p.avg_cost)}
                </td>
                <td className="px-4 py-3 text-right font-mono tabular-nums">
                  {price != null ? formatCurrency(price) : "—"}
                </td>
                <td className="px-4 py-3 text-right font-mono tabular-nums">
                  {marketValue !== null ? formatCurrency(marketValue) : "—"}
                </td>
                <td
                  className="px-4 py-3 text-right font-mono tabular-nums"
                  style={{
                    color:
                      unrealizedPnl === null
                        ? undefined
                        : unrealizedPnl >= 0
                          ? "var(--gain)"
                          : "var(--loss)",
                  }}
                >
                  {unrealizedPnl !== null && unrealizedPnlPct !== null
                    ? `${unrealizedPnl >= 0 ? "+" : ""}${formatCurrency(unrealizedPnl)} (${unrealizedPnlPct.toFixed(2)}%)`
                    : "—"}
                </td>
                <td
                  className="px-4 py-3 text-right font-mono tabular-nums"
                  style={{
                    color:
                      changePct === null ? undefined : changePct >= 0 ? "var(--gain)" : "var(--loss)",
                  }}
                >
                  {changePct !== null ? `${changePct >= 0 ? "+" : ""}${changePct.toFixed(2)}%` : "—"}
                </td>
                <td className="px-4 py-3">
                  <TradeTicket ticker={p.ticker} lockTicker side="sell" maxQuantity={p.quantity} compact />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
