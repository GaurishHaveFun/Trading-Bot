"use client";

import { useEffect, useRef } from "react";
import {
  AreaSeries,
  ColorType,
  createChart,
  type IChartApi,
  type UTCTimestamp,
} from "lightweight-charts";
import type { PaperAccountHistoryRow } from "@/lib/types";

/**
 * Area chart of total account value over time, wrapping TradingView's
 * lightweight-charts (v5 API: `chart.addSeries(AreaSeries, options)`).
 * Structurally mirrors components/PriceChart.tsx: colors pulled from the
 * design system's CSS custom properties, transparent background so it sits
 * inside the glass panel, faint grid lines, resize handling, cleanup on
 * unmount.
 *
 * Line/fill color follows brokerage-app convention: green (--gain) if the
 * account's value net-increased from the first snapshot to the last, red
 * (--loss) if it net-decreased.
 */
export default function AccountValueChart({ history }: { history: PaperAccountHistoryRow[] }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || history.length < 2) return;

    const styles = getComputedStyle(document.documentElement);
    const gain = styles.getPropertyValue("--gain").trim() || "#34d399";
    const loss = styles.getPropertyValue("--loss").trim() || "#f87171";
    const foregroundMuted = styles.getPropertyValue("--foreground-muted").trim() || "#a1a1aa";
    const gridColor = "rgba(255, 255, 255, 0.05)";
    const borderColor = "rgba(255, 255, 255, 0.14)";

    const trendedUp = history[history.length - 1].total_value >= history[0].total_value;
    const trendColor = trendedUp ? gain : loss;

    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: "rgba(0, 0, 0, 0)" },
        textColor: foregroundMuted,
      },
      grid: {
        vertLines: { color: gridColor },
        horzLines: { color: gridColor },
      },
      width: container.clientWidth,
      height: 280,
      timeScale: { timeVisible: true, borderColor },
      rightPriceScale: { borderColor },
    });
    chartRef.current = chart;

    const series = chart.addSeries(AreaSeries, {
      lineColor: trendColor,
      topColor: `${trendColor}33`,
      bottomColor: `${trendColor}00`,
      lineWidth: 2,
    });

    series.setData(
      history.map((point) => ({
        time: (Math.floor(new Date(point.recorded_at).getTime() / 1000) as UTCTimestamp),
        value: point.total_value,
      }))
    );

    chart.timeScale().fitContent();

    const handleResize = () => {
      if (!containerRef.current) return;
      chart.applyOptions({ width: containerRef.current.clientWidth });
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
      chartRef.current = null;
    };
  }, [history]);

  if (history.length < 2) {
    return (
      <p className="text-sm text-foreground-muted">
        Not enough history yet — check back after a few visits/trades.
      </p>
    );
  }

  return <div ref={containerRef} className="w-full" />;
}
