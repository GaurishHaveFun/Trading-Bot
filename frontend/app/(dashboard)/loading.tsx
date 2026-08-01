/**
 * Instant loading state for the Portfolio home page while
 * getAccount()/getPositions()/getTrades()/getQuotes() resolve against the
 * Render-hosted backend (can take up to ~30s on cold start). Mirrors
 * PortfolioSummary's hero block, the Buy panel, PositionsTable's 8-column
 * layout, and TradesList's 6-column layout so the swap-in doesn't jump.
 */
export default function PortfolioLoading() {
  const holdingRows = Array.from({ length: 4 });
  const tradeRows = Array.from({ length: 5 });

  return (
    <div className="flex flex-col gap-6">
      {/* Hero summary */}
      <section className="glass-panel-dense p-6 sm:p-8">
        <div className="h-3 w-28 animate-pulse rounded bg-white/10" />
        <div className="mt-3 h-10 w-56 animate-pulse rounded bg-white/10 sm:h-14 sm:w-72" />
        <div className="mt-5 flex flex-wrap items-center gap-x-6 gap-y-2">
          <div className="h-4 w-48 animate-pulse rounded bg-white/10" />
          <div className="h-4 w-32 animate-pulse rounded bg-white/10" />
        </div>
      </section>

      {/* Buy panel */}
      <section className="glass-panel p-4 sm:p-6">
        <div className="mb-3 h-5 w-16 animate-pulse rounded bg-white/10" />
        <div className="flex flex-wrap gap-3">
          <div className="h-9 w-32 animate-pulse rounded bg-white/10" />
          <div className="h-9 w-24 animate-pulse rounded bg-white/10" />
          <div className="h-9 w-20 animate-pulse rounded bg-white/10" />
        </div>
      </section>

      {/* Holdings */}
      <section className="flex flex-col gap-2">
        <div className="h-5 w-24 animate-pulse rounded bg-white/10" />
        <div className="glass-panel-dense overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="border-b border-white/10">
              <tr>
                <th className="px-4 py-3 text-left font-medium text-foreground-muted">Ticker</th>
                <th className="px-4 py-3 text-right font-medium text-foreground-muted">Qty</th>
                <th className="px-4 py-3 text-right font-medium text-foreground-muted">Avg cost</th>
                <th className="px-4 py-3 text-right font-medium text-foreground-muted">Price</th>
                <th className="px-4 py-3 text-right font-medium text-foreground-muted">Mkt value</th>
                <th className="px-4 py-3 text-right font-medium text-foreground-muted">
                  Unrealized P&amp;L
                </th>
                <th className="px-4 py-3 text-right font-medium text-foreground-muted">Day %</th>
                <th className="px-4 py-3 text-right font-medium text-foreground-muted">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/10">
              {holdingRows.map((_, i) => (
                <tr key={i} className="animate-pulse">
                  <td className="px-4 py-3">
                    <div className="h-4 w-14 rounded bg-white/10" />
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="ml-auto h-4 w-8 rounded bg-white/10" />
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="ml-auto h-4 w-14 rounded bg-white/10" />
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="ml-auto h-4 w-14 rounded bg-white/10" />
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="ml-auto h-4 w-16 rounded bg-white/10" />
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="ml-auto h-4 w-20 rounded bg-white/10" />
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="ml-auto h-4 w-12 rounded bg-white/10" />
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="ml-auto h-7 w-16 rounded bg-white/10" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Recent trades */}
      <section className="flex flex-col gap-2">
        <div className="h-5 w-32 animate-pulse rounded bg-white/10" />
        <div className="glass-panel-dense overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="border-b border-white/10">
              <tr>
                <th className="px-4 py-3 text-left font-medium text-foreground-muted">Ticker</th>
                <th className="px-4 py-3 text-left font-medium text-foreground-muted">Side</th>
                <th className="px-4 py-3 text-right font-medium text-foreground-muted">Qty</th>
                <th className="px-4 py-3 text-right font-medium text-foreground-muted">Price</th>
                <th className="px-4 py-3 text-right font-medium text-foreground-muted">
                  Realized P&amp;L
                </th>
                <th className="px-4 py-3 text-left font-medium text-foreground-muted">Time</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/10">
              {tradeRows.map((_, i) => (
                <tr key={i} className="animate-pulse">
                  <td className="px-4 py-3">
                    <div className="h-4 w-14 rounded bg-white/10" />
                  </td>
                  <td className="px-4 py-3">
                    <div className="h-4 w-10 rounded bg-white/10" />
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="ml-auto h-4 w-8 rounded bg-white/10" />
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="ml-auto h-4 w-14 rounded bg-white/10" />
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="ml-auto h-4 w-16 rounded bg-white/10" />
                  </td>
                  <td className="px-4 py-3">
                    <div className="h-4 w-24 rounded bg-white/10" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
