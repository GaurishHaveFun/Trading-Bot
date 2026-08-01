import AccountValueChart from "@/components/AccountValueChart";
import PortfolioSummary from "@/components/PortfolioSummary";
import PositionsTable from "@/components/PositionsTable";
import TradesList from "@/components/TradesList";
import TradeTicket from "@/components/TradeTicket";
import { getAccount, getAccountHistory, getPositions, getTrades, recordAccountSnapshot } from "@/lib/paper";
import { getQuotes } from "@/lib/quotes";
import type { PaperAccountRow, PaperPositionRow, PaperTradeRow, QuoteRow } from "@/lib/types";

// Account/position/quote data changes on every trade and every price tick —
// always render at request time, same rationale as the old screener home.
export const dynamic = "force-dynamic";

interface PortfolioData {
  account: PaperAccountRow;
  positions: PaperPositionRow[];
  trades: PaperTradeRow[];
  quotes: QuoteRow[];
}

async function loadPortfolio(): Promise<{ data: PortfolioData | null; error: string | null }> {
  try {
    const [account, positions, trades] = await Promise.all([
      getAccount(),
      getPositions(),
      getTrades(10),
    ]);
    // getQuotes never throws (see lib/quotes.ts) — a screener-API outage
    // yields [], which PositionsTable already renders as "—" per row.
    const quotes = await getQuotes(positions.map((p) => p.ticker));
    return { data: { account, positions, trades, quotes }, error: null };
  } catch (err) {
    console.error("Failed to load portfolio", err);
    return {
      data: null,
      error: "Could not load your portfolio. Check that DATABASE_URL is configured and reachable.",
    };
  }
}

export default async function PortfolioPage() {
  const { data, error } = await loadPortfolio();

  if (error || !data) {
    return (
      <div className="glass-panel px-4 py-3 text-sm" style={{ color: "var(--loss)" }}>
        {error}
      </div>
    );
  }

  const { account, positions, trades, quotes } = data;
  const quoteByTicker = new Map(quotes.map((q) => [q.ticker, q]));

  let marketValue = 0;
  let costBasis = 0;
  for (const p of positions) {
    const quote = quoteByTicker.get(p.ticker);
    // Fall back to cost basis when a live quote is missing, so a single
    // unreachable symbol degrades the hero total gracefully instead of
    // producing NaN — the per-row table still shows "—" for that ticker.
    marketValue += (quote?.price ?? p.avg_cost) * p.quantity;
    costBasis += p.avg_cost * p.quantity;
  }
  const totalValue = account.cash_balance + marketValue;
  const unrealizedPnl = marketValue - costBasis;
  const unrealizedPnlPct = costBasis > 0 ? (unrealizedPnl / costBasis) * 100 : 0;

  // Snapshot the account's current value on every page render — see
  // lib/paper.ts recordAccountSnapshot() docstring for why this is done here
  // rather than inside buyStock/sellStock.
  await recordAccountSnapshot(totalValue, account.cash_balance, marketValue);
  const history = await getAccountHistory();

  return (
    <div className="flex flex-col gap-6">
      <PortfolioSummary
        totalValue={totalValue}
        cashBalance={account.cash_balance}
        unrealizedPnl={unrealizedPnl}
        unrealizedPnlPct={unrealizedPnlPct}
      />

      <section className="glass-panel panel-enter p-4 sm:p-6">
        <h2 className="mb-3 font-display text-lg font-semibold text-foreground">Account value over time</h2>
        <AccountValueChart history={history} />
      </section>

      <section className="glass-panel panel-enter p-4 sm:p-6">
        <h2 className="mb-3 font-display text-lg font-semibold text-foreground">Buy</h2>
        <TradeTicket side="buy" />
      </section>

      <section className="flex flex-col gap-2">
        <h2 className="font-display text-lg font-semibold text-foreground">Holdings</h2>
        <PositionsTable positions={positions} quotes={quoteByTicker} />
      </section>

      <section className="flex flex-col gap-2">
        <h2 className="font-display text-lg font-semibold text-foreground">Recent trades</h2>
        <TradesList trades={trades} />
      </section>
    </div>
  );
}
