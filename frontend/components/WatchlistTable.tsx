import TradeTicket from "./TradeTicket";
import type { LoserRow } from "@/lib/types";

function formatCurrency(value: number): string {
  return value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  });
}

/** Compact human-readable market cap, e.g. "$1.2B". */
function formatMarketCap(value: number): string {
  if (value >= 1e12) return `$${(value / 1e12).toFixed(2)}T`;
  if (value >= 1e9) return `$${(value / 1e9).toFixed(2)}B`;
  if (value >= 1e6) return `$${(value / 1e6).toFixed(2)}M`;
  return formatCurrency(value);
}

/**
 * Today's biggest losers, each with an inline quick-buy TradeTicket. The
 * change_pct color logic stays generic (green if >= 0) even though this
 * list is sourced from /losers and every row is expected to be negative —
 * a value can legitimately turn non-negative between fetch and render.
 */
export default function WatchlistTable({ losers }: { losers: LoserRow[] }) {
  if (losers.length === 0) {
    return (
      <div className="glass-panel px-4 py-6 text-center text-sm text-foreground-muted">
        No watchlist data available right now. The screener service may be unreachable.
      </div>
    );
  }

  return (
    <div className="glass-panel-dense panel-enter overflow-x-auto">
      <table className="min-w-full text-sm">
        <thead className="border-b border-white/10">
          <tr>
            <th className="px-4 py-3 text-left font-medium text-foreground-muted">Ticker</th>
            <th className="px-4 py-3 text-right font-medium text-foreground-muted">Price</th>
            <th className="px-4 py-3 text-right font-medium text-foreground-muted">Change %</th>
            <th className="px-4 py-3 text-right font-medium text-foreground-muted">Mkt cap</th>
            <th className="px-4 py-3 text-right font-medium text-foreground-muted">Action</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/10">
          {losers.map((l) => (
            <tr key={l.ticker} className="glass-row">
              <td className="px-4 py-3 font-mono font-medium text-foreground">{l.ticker}</td>
              <td className="px-4 py-3 text-right font-mono tabular-nums">
                {formatCurrency(l.price)}
              </td>
              <td
                className="px-4 py-3 text-right font-mono tabular-nums"
                style={{ color: l.change_pct >= 0 ? "var(--gain)" : "var(--loss)" }}
              >
                {l.change_pct >= 0 ? "+" : ""}
                {l.change_pct.toFixed(2)}%
              </td>
              <td className="px-4 py-3 text-right font-mono tabular-nums text-foreground-muted">
                {formatMarketCap(l.market_cap)}
              </td>
              <td className="px-4 py-3">
                <TradeTicket ticker={l.ticker} lockTicker side="buy" compact />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
