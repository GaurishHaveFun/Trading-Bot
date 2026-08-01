import WatchlistTable from "@/components/WatchlistTable";
import { getLosers } from "@/lib/quotes";

// Live-ish price/loser data, same rationale as the rest of the dashboard —
// never serve a static shell.
export const dynamic = "force-dynamic";

export default async function WatchlistPage() {
  // getLosers (lib/quotes.ts) already swallows network/HTTP failures and
  // returns [] rather than throwing, so "service unreachable" and "no
  // losers today" both land here as an empty list — WatchlistTable renders
  // a single empty-state message that covers both cases.
  const losers = await getLosers(20);

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="font-display text-2xl font-semibold text-foreground">Watchlist</h1>
        <p className="text-sm text-foreground-muted">
          Today&apos;s biggest losers — quick paper-trade ideas.
        </p>
      </div>
      <WatchlistTable losers={losers} />
    </div>
  );
}
