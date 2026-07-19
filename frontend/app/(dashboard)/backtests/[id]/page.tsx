import { notFound } from "next/navigation";
import BacktestStats from "@/components/BacktestStats";
import { getBacktest } from "@/lib/queries";
import type { BacktestDetail } from "@/lib/types";

export const dynamic = "force-dynamic";

async function loadBacktest(
  id: number
): Promise<{ backtest: BacktestDetail | null; error: string | null }> {
  try {
    const backtest = await getBacktest(id);
    return { backtest, error: null };
  } catch (err) {
    console.error("Failed to load backtest", err);
    return {
      backtest: null,
      error:
        "Could not load this backtest. Check that DATABASE_URL is configured and reachable.",
    };
  }
}

export default async function BacktestDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const backtestId = Number(id);

  if (!Number.isFinite(backtestId)) {
    notFound();
  }

  const { backtest, error } = await loadBacktest(backtestId);

  if (error) {
    return (
      <div className="glass-panel px-4 py-3 text-sm" style={{ color: "var(--loss)" }}>
        {error}
      </div>
    );
  }

  if (!backtest) {
    notFound();
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="font-display text-2xl font-semibold text-foreground">
          Backtest #{backtest.id}
        </h1>
        <p className="text-sm text-foreground-muted">
          {backtest.created_at} · Universe: {backtest.universe ?? "—"} · Holding days:{" "}
          <span className="font-mono">{backtest.holding_days ?? "—"}</span>
        </p>
      </div>

      <BacktestStats backtest={backtest} />

      <section className="flex flex-col gap-2">
        <h2 className="font-display text-lg font-semibold text-foreground">
          Rule attributions
        </h2>
        {backtest.rule_attributions.length === 0 ? (
          <p className="text-sm text-foreground-muted">
            No rule attribution data for this backtest.
          </p>
        ) : (
          <div className="glass-panel-dense panel-enter overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="border-b border-white/10">
                <tr>
                  <th className="px-4 py-3 text-left font-medium text-foreground-muted">
                    Rule
                  </th>
                  <th className="px-4 py-3 text-right font-medium text-foreground-muted">
                    Weight
                  </th>
                  <th className="px-4 py-3 text-right font-medium text-foreground-muted">
                    Passed (count · win% / avg ret)
                  </th>
                  <th className="px-4 py-3 text-right font-medium text-foreground-muted">
                    Failed (count · win% / avg ret)
                  </th>
                  <th className="px-4 py-3 text-right font-medium text-foreground-muted">
                    Edge %
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/10">
                {backtest.rule_attributions.map((ra) => (
                  <tr key={ra.id} className="glass-row">
                    <td className="px-4 py-3 font-medium text-foreground">{ra.rule_name}</td>
                    <td className="px-4 py-3 text-right font-mono tabular-nums">
                      {ra.weight ?? "—"}
                    </td>
                    <td className="px-4 py-3 text-right font-mono tabular-nums text-foreground-muted">
                      {ra.passed_count ?? "—"} ·{" "}
                      {ra.passed_win_rate !== null ? `${ra.passed_win_rate.toFixed(1)}%` : "—"} /{" "}
                      {ra.passed_avg_return_pct !== null
                        ? `${ra.passed_avg_return_pct.toFixed(2)}%`
                        : "—"}
                    </td>
                    <td className="px-4 py-3 text-right font-mono tabular-nums text-foreground-muted">
                      {ra.failed_count ?? "—"} ·{" "}
                      {ra.failed_win_rate !== null ? `${ra.failed_win_rate.toFixed(1)}%` : "—"} /{" "}
                      {ra.failed_avg_return_pct !== null
                        ? `${ra.failed_avg_return_pct.toFixed(2)}%`
                        : "—"}
                    </td>
                    <td
                      className="px-4 py-3 text-right font-mono tabular-nums font-medium"
                      style={{
                        color: (ra.edge_pct ?? 0) >= 0 ? "var(--gain)" : "var(--loss)",
                      }}
                    >
                      {ra.edge_pct !== null ? `${ra.edge_pct.toFixed(2)}%` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="flex flex-col gap-2">
        <h2 className="font-display text-lg font-semibold text-foreground">Trades</h2>
        {backtest.trades.length === 0 ? (
          <p className="text-sm text-foreground-muted">No trades recorded for this backtest.</p>
        ) : (
          <div className="glass-panel-dense panel-enter overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="border-b border-white/10">
                <tr>
                  <th className="px-4 py-3 text-left font-medium text-foreground-muted">
                    Ticker
                  </th>
                  <th className="px-4 py-3 text-left font-medium text-foreground-muted">
                    Signal date
                  </th>
                  <th className="px-4 py-3 text-right font-medium text-foreground-muted">
                    Buy close
                  </th>
                  <th className="px-4 py-3 text-left font-medium text-foreground-muted">
                    Sell date
                  </th>
                  <th className="px-4 py-3 text-right font-medium text-foreground-muted">
                    Sell close
                  </th>
                  <th className="px-4 py-3 text-right font-medium text-foreground-muted">
                    Return %
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/10">
                {backtest.trades.map((trade) => (
                  <tr
                    key={trade.id}
                    className="glass-row"
                    style={{
                      backgroundColor:
                        trade.win === true
                          ? "color-mix(in srgb, var(--gain) 12%, transparent)"
                          : trade.win === false
                            ? "color-mix(in srgb, var(--loss) 12%, transparent)"
                            : undefined,
                    }}
                  >
                    <td className="px-4 py-3 font-mono font-medium text-foreground">
                      {trade.ticker}
                    </td>
                    <td className="px-4 py-3 font-mono text-foreground-muted">
                      {trade.signal_date}
                    </td>
                    <td className="px-4 py-3 text-right font-mono tabular-nums">
                      {trade.buy_close ?? "—"}
                    </td>
                    <td className="px-4 py-3 font-mono text-foreground-muted">
                      {trade.sell_date ?? "—"}
                    </td>
                    <td className="px-4 py-3 text-right font-mono tabular-nums">
                      {trade.sell_close ?? "—"}
                    </td>
                    <td
                      className="px-4 py-3 text-right font-mono tabular-nums font-medium"
                      style={{
                        color: (trade.return_pct ?? 0) >= 0 ? "var(--gain)" : "var(--loss)",
                      }}
                    >
                      {trade.return_pct !== null ? `${trade.return_pct.toFixed(2)}%` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
