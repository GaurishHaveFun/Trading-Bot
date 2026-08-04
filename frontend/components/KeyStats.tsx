import Tile from "./Tile";
import { formatCompact, formatNum, formatVolume } from "@/lib/format";
import type { TickerStats } from "@/lib/types";

/**
 * Grid of key fundamentals/trading stats for a ticker, sourced from the
 * live GET /tickers/{symbol} response. Every field is nullable (yfinance
 * omits fields constantly) — nulls render as "—" and the whole component
 * returns null when every stat is null, so an outage doesn't leave a
 * ghost grid of dashes on the page.
 */
export default function KeyStats({ stats }: { stats: TickerStats }) {
  const allNull = Object.values(stats).every((v) => v === null || v === undefined);
  if (allNull) return null;

  const hasRange = stats.fifty_two_week_low !== null && stats.fifty_two_week_high !== null;
  const hasDayRange = stats.day_low !== null && stats.day_high !== null;

  // dividendYield's unit is yfinance-version-dependent: some versions report
  // a fraction (0.0044 == 0.44%), others already report a percent (0.44 ==
  // 0.44%). Heuristic: values under 1 are almost certainly a fraction that
  // needs *100; values >= 1 are already a percent. Not bulletproof for a
  // sub-1%-yield stock reported as a percent, but that's a rare edge case
  // versus the common fraction case.
  const dividendPct =
    stats.dividend_yield !== null && stats.dividend_yield !== undefined
      ? stats.dividend_yield < 1
        ? stats.dividend_yield * 100
        : stats.dividend_yield
      : null;

  const volVsAvg =
    stats.volume !== null &&
    stats.volume !== undefined &&
    stats.avg_volume !== null &&
    stats.avg_volume !== undefined &&
    stats.avg_volume !== 0
      ? stats.volume / stats.avg_volume
      : null;

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
      <Tile label="Market cap" value={formatCompact(stats.market_cap)} />
      <Tile label="Trailing P/E" value={formatNum(stats.trailing_pe)} />
      <Tile label="Forward P/E" value={formatNum(stats.forward_pe)} />
      <Tile label="P/B" value={formatNum(stats.price_to_book)} />
      <Tile label="EPS (TTM)" value={formatNum(stats.eps_trailing)} />
      <Tile label="Dividend yield" value={dividendPct !== null ? `${formatNum(dividendPct)}%` : "—"} />
      <Tile label="Beta" value={formatNum(stats.beta)} />
      <Tile
        label="52W range"
        value={hasRange ? `${formatNum(stats.fifty_two_week_low)} – ${formatNum(stats.fifty_two_week_high)}` : "—"}
      />
      <Tile
        label="Day range"
        value={hasDayRange ? `${formatNum(stats.day_low)} – ${formatNum(stats.day_high)}` : "—"}
      />
      <Tile label="Volume" value={formatVolume(stats.volume)} />
      <Tile label="Avg volume" value={formatVolume(stats.avg_volume)} />
      <Tile
        label="Vol vs avg"
        value={volVsAvg !== null ? `${formatNum(volVsAvg, 1)}×` : "—"}
        tone={volVsAvg === null ? "neutral" : volVsAvg >= 1 ? "pos" : "neg"}
      />
    </div>
  );
}
