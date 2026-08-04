import Link from "next/link";
import AnalystTargets from "@/components/AnalystTargets";
import CompanyProfile from "@/components/CompanyProfile";
import KeyStats from "@/components/KeyStats";
import PriceChart from "@/components/PriceChart";
import PriceStats from "@/components/PriceStats";
import RefreshButton from "@/components/RefreshButton";
import ScoreGauge from "@/components/ScoreGauge";
import { formatNum } from "@/lib/format";
import { getTickerHistory } from "@/lib/queries";
import { getTickerDetail } from "@/lib/quotes";
import type { TickerHistory } from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadHistory(
  ticker: string
): Promise<{ history: TickerHistory | null; error: string | null }> {
  try {
    const history = await getTickerHistory(ticker);
    return { history, error: null };
  } catch (err) {
    console.error("Failed to load ticker history", err);
    return {
      history: null,
      error:
        "Could not load history for this ticker. Check that DATABASE_URL is configured and reachable.",
    };
  }
}

export default async function TickerPage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol } = await params;
  const ticker = symbol.toUpperCase();

  const [detail, { history, error }] = await Promise.all([
    getTickerDetail(ticker),
    loadHistory(ticker),
  ]);

  // A successful API call returning bars: [] (e.g. a delisted symbol) should
  // still fall back to whatever the DB has, so this checks .length, not
  // nullish-coalescing on the array itself.
  const chartBars = detail?.bars.length ? detail.bars : (history?.bars ?? []);

  // Both live API and DB are down: nothing to render at all.
  if (!detail && !history) {
    return (
      <div className="flex flex-col gap-4">
        <div>
          <h1 className="font-display text-2xl font-semibold text-foreground">{ticker}</h1>
          <p className="text-sm text-foreground-muted">Score history and price chart.</p>
        </div>
        <div className="glass-panel px-4 py-3 text-sm" style={{ color: "var(--loss)" }}>
          {error ??
            "Could not load data for this ticker. The screener API and database are both unreachable."}
        </div>
      </div>
    );
  }

  const stats = detail?.stats;
  const profile = detail?.profile;
  const changeTone: "pos" | "neg" | "neutral" =
    stats?.change_pct === null || stats?.change_pct === undefined
      ? "neutral"
      : stats.change_pct >= 0
        ? "pos"
        : "neg";

  const pills = [profile?.sector, profile?.industry, profile?.exchange].filter(
    (p): p is string => Boolean(p)
  );

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-2xl font-semibold text-foreground">{ticker}</h1>
          {profile?.long_name && (
            <p className="text-sm text-foreground-muted">{profile.long_name}</p>
          )}
          {pills.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {pills.map((p) => (
                <span
                  key={p}
                  className="inline-block rounded-md bg-gradient-accent px-1.5 py-0.5 text-[10px] font-semibold uppercase text-[#0a0b14]"
                >
                  {p}
                </span>
              ))}
            </div>
          )}
        </div>

        {stats && stats.price !== null && (
          <div className="text-right">
            <div className="font-mono text-2xl font-semibold text-foreground">
              {formatNum(stats.price)}
            </div>
            {stats.change_pct !== null && (
              <div
                className="font-mono text-sm"
                style={{ color: changeTone === "pos" ? "var(--gain)" : "var(--loss)" }}
              >
                {stats.change_pct >= 0 ? "+" : ""}
                {formatNum(stats.change_pct)}%
              </div>
            )}
          </div>
        )}
      </div>

      {/*
        Error banner intentionally does NOT render when `detail` is present:
        an "API ok, DB down/empty" state is the normal case for a ticker
        with no screener history and must render silently (see the plan's
        four-state table). `error` can only be non-null here when `history`
        is null, which combined with `detail` existing is exactly that case.
      */}
      {!detail && (
        <div className="glass-panel px-4 py-3 text-sm text-foreground-muted">
          Live company data unavailable — showing stored history.
        </div>
      )}

      <div className="glass-panel panel-enter p-4">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-medium text-foreground-muted">Price Chart</h2>
          <RefreshButton />
        </div>
        <PriceChart bars={chartBars} />
      </div>

      {stats && <KeyStats stats={stats} />}

      {detail && <AnalystTargets targets={detail.targets} price={stats?.price ?? null} />}

      {profile && <CompanyProfile profile={profile} />}

      {history && history.signals.length > 0 && (
        <>
          <PriceStats
            bars={history.bars}
            latestSnapshot={history.signals[0]?.signal.snapshot}
            showPriceRow={false}
          />

          <div className="glass-panel-dense panel-enter overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="border-b border-white/10">
                <tr>
                  <th className="px-4 py-3 text-left font-medium text-foreground-muted">
                    Run timestamp
                  </th>
                  <th className="px-4 py-3 text-left font-medium text-foreground-muted">
                    Score
                  </th>
                  <th className="px-4 py-3 text-right font-medium text-foreground-muted">
                    Rules
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/10">
                {history.signals.map(({ signal, run }) => (
                  <tr key={signal.id} className="glass-row">
                    <td className="px-4 py-3">
                      <Link
                        href={`/screener/runs/${encodeURIComponent(run.run_timestamp)}`}
                        className="font-mono text-foreground hover:text-gradient-accent hover:underline"
                      >
                        {run.run_timestamp}
                      </Link>
                    </td>
                    <td className="px-4 py-3">
                      <ScoreGauge
                        score={signal.score}
                        threshold={run.alert_threshold ?? 0.7}
                        size="sm"
                      />
                    </td>
                    <td className="px-4 py-3 text-right font-mono tabular-nums text-foreground-muted">
                      {signal.rules_passed ?? "—"}/{signal.rules_total ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
