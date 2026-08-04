import Tile from "./Tile";
import { formatNum, formatVolume } from "@/lib/format";
import type { BarRow, SignalSnapshot } from "@/lib/types";

/** Small "above"/"below" indicator relative to a moving average, gain/loss colored. */
function AboveBelow({ close, ma }: { close: number; ma: number }) {
  const above = close > ma;
  return (
    <span
      className="ml-2 text-xs font-medium"
      style={{ color: above ? "var(--gain)" : "var(--loss)" }}
    >
      {above ? "▲ above" : "▼ below"}
    </span>
  );
}

/**
 * Stat strip for a ticker's price history: last price, change, 52-week
 * range, and volume from the raw bars, plus indicator/fundamentals stats
 * from the most recent signal snapshot when available. Fully defensive —
 * renders "—" or omits sections when data is missing rather than crashing.
 *
 * `showPriceRow` hides the first grid (last price / change / 52W range /
 * volume) — used on the ticker detail page where KeyStats already covers
 * that ground and this component's remaining value is the second,
 * screener-derived grid (RSI / SMA 50 / SMA 200 / ATR / P/B).
 */
export default function PriceStats({
  bars,
  latestSnapshot,
  showPriceRow = true,
}: {
  bars: BarRow[];
  latestSnapshot?: SignalSnapshot | null;
  showPriceRow?: boolean;
}) {
  if (bars.length === 0) {
    return null;
  }

  const last = bars[bars.length - 1];
  const prev = bars.length >= 2 ? bars[bars.length - 2] : null;

  const changeAbs = prev ? last.close - prev.close : null;
  const changePct = prev && prev.close !== 0 ? (changeAbs! / prev.close) * 100 : null;
  const changeTone: "pos" | "neg" | "neutral" =
    changeAbs === null ? "neutral" : changeAbs >= 0 ? "pos" : "neg";

  const low52 = bars.reduce((min, b) => Math.min(min, b.low), bars[0].low);
  const high52 = bars.reduce((max, b) => Math.max(max, b.high), bars[0].high);

  const rsi = latestSnapshot?.rsi_14;
  const sma50 = latestSnapshot?.sma_50;
  const sma200 = latestSnapshot?.sma_200;
  const atr = latestSnapshot?.atr_14;
  const pb = latestSnapshot?.price_to_book;
  const pbDisplay = typeof pb === "number" && Number.isFinite(pb) ? formatNum(pb) : "—";
  const industry = latestSnapshot?.industry;

  return (
    <div className="flex flex-col gap-3">
      {showPriceRow && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          <Tile
            label="Last price"
            value={formatNum(last.close)}
            valueClassName="text-2xl"
          />
          <Tile
            label="Change"
            value={
              changeAbs === null ? (
                "—"
              ) : (
                <>
                  {changeAbs >= 0 ? "+" : ""}
                  {formatNum(changeAbs)} ({changePct! >= 0 ? "+" : ""}
                  {formatNum(changePct)}%)
                </>
              )
            }
            tone={changeTone}
          />
          <Tile label="52W Range" value={`${formatNum(low52)} – ${formatNum(high52)}`} />
          <Tile label="Volume" value={formatVolume(last.volume)} />
        </div>
      )}

      {latestSnapshot && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {rsi !== null && rsi !== undefined && (
            <Tile label="RSI (14)" value={formatNum(rsi, 1)} />
          )}
          {sma50 !== null && sma50 !== undefined && (
            <Tile
              label="SMA 50"
              value={
                <>
                  {formatNum(sma50)}
                  <AboveBelow close={last.close} ma={sma50} />
                </>
              }
            />
          )}
          {sma200 !== null && sma200 !== undefined && (
            <Tile
              label="SMA 200"
              value={
                <>
                  {formatNum(sma200)}
                  <AboveBelow close={last.close} ma={sma200} />
                </>
              }
            />
          )}
          {atr !== null && atr !== undefined && <Tile label="ATR (14)" value={formatNum(atr)} />}
          {pb !== null && pb !== undefined && <Tile label="P/B" value={pbDisplay} />}
          {industry && (
            <Tile
              label="Industry"
              value={
                <span className="inline-block rounded-md bg-gradient-accent px-1.5 py-0.5 text-[10px] font-semibold uppercase text-[#0a0b14]">
                  {industry}
                </span>
              }
              valueClassName="text-sm"
            />
          )}
        </div>
      )}
    </div>
  );
}
