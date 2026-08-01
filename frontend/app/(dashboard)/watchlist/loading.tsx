/**
 * Instant loading state for /watchlist while getLosers() resolves against
 * the Render-hosted screener API (can take up to ~30s on cold start).
 * Mirrors WatchlistTable's 5-column layout so the swap-in doesn't jump.
 */
export default function WatchlistLoading() {
  const rows = Array.from({ length: 10 });

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="font-display text-2xl font-semibold text-foreground">Watchlist</h1>
        <p className="text-sm text-foreground-muted">
          Today&apos;s biggest losers — quick paper-trade ideas.
        </p>
      </div>

      <div className="glass-panel-dense overflow-x-auto">
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
            {rows.map((_, i) => (
              <tr key={i} className="animate-pulse">
                <td className="px-4 py-3">
                  <div className="h-4 w-14 rounded bg-white/10" />
                </td>
                <td className="px-4 py-3 text-right">
                  <div className="ml-auto h-4 w-16 rounded bg-white/10" />
                </td>
                <td className="px-4 py-3 text-right">
                  <div className="ml-auto h-4 w-12 rounded bg-white/10" />
                </td>
                <td className="px-4 py-3 text-right">
                  <div className="ml-auto h-4 w-14 rounded bg-white/10" />
                </td>
                <td className="px-4 py-3 text-right">
                  <div className="ml-auto h-7 w-16 rounded bg-white/10" />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
