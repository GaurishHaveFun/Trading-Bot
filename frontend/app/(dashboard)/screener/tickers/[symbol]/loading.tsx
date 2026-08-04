/**
 * Instant loading state for /screener/tickers/[symbol] while getTickerDetail()
 * and getTickerHistory() resolve — matters more now that the page waits on
 * the Render-hosted screener API's cold start (~30s). No props are
 * available to a loading.tsx, so the header is a skeleton bar rather than
 * the actual symbol. Mirrors watchlist/loading.tsx's animate-pulse +
 * bg-white/10 idiom.
 */
export default function TickerDetailLoading() {
  const tiles = Array.from({ length: 8 });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-4">
        <div className="animate-pulse">
          <div className="h-8 w-24 rounded bg-white/10" />
          <div className="mt-2 h-4 w-40 rounded bg-white/10" />
          <div className="mt-2 flex gap-1.5">
            <div className="h-4 w-16 rounded bg-white/10" />
            <div className="h-4 w-16 rounded bg-white/10" />
          </div>
        </div>
        <div className="animate-pulse text-right">
          <div className="ml-auto h-7 w-20 rounded bg-white/10" />
          <div className="ml-auto mt-2 h-4 w-14 rounded bg-white/10" />
        </div>
      </div>

      <div className="glass-panel h-[400px] animate-pulse p-4">
        <div className="h-4 w-24 rounded bg-white/10" />
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {tiles.map((_, i) => (
          <div key={i} className="glass-panel-dense animate-pulse p-4">
            <div className="h-3 w-16 rounded bg-white/10" />
            <div className="mt-2 h-5 w-12 rounded bg-white/10" />
          </div>
        ))}
      </div>

      <div className="glass-panel animate-pulse p-4">
        <div className="h-3 w-32 rounded bg-white/10" />
        <div className="mt-3 h-3 w-full rounded bg-white/10" />
        <div className="mt-2 h-3 w-full rounded bg-white/10" />
        <div className="mt-2 h-3 w-2/3 rounded bg-white/10" />
        <div className="mt-2 h-3 w-1/2 rounded bg-white/10" />
      </div>
    </div>
  );
}
