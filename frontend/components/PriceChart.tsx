"use client";

import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  type IChartApi,
  type UTCTimestamp,
} from "lightweight-charts";
import type { BarRow } from "@/lib/types";

/**
 * Candlestick price chart, wrapping TradingView's lightweight-charts (v5 API:
 * `chart.addSeries(CandlestickSeries, options)` rather than the older
 * `chart.addCandlestickSeries()`). Colors are pulled from the design system's
 * CSS custom properties (gain/loss/foreground-muted). The chart background is
 * transparent so it sits inside the glass panel and the ambient background
 * glow shows through; grid lines are kept very faint for legibility.
 */
export default function PriceChart({ bars }: { bars: BarRow[] }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const styles = getComputedStyle(document.documentElement);
    const gain = styles.getPropertyValue("--gain").trim() || "#34d399";
    const loss = styles.getPropertyValue("--loss").trim() || "#f87171";
    const foregroundMuted = styles.getPropertyValue("--foreground-muted").trim() || "#a1a1aa";
    const gridColor = "rgba(255, 255, 255, 0.05)";
    const borderColor = "rgba(255, 255, 255, 0.14)";

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
      height: 360,
      timeScale: { timeVisible: true, borderColor },
      rightPriceScale: { borderColor },
    });
    chartRef.current = chart;

    const series = chart.addSeries(CandlestickSeries, {
      upColor: gain,
      downColor: loss,
      borderVisible: false,
      wickUpColor: gain,
      wickDownColor: loss,
    });

    series.setData(
      bars.map((bar) => ({
        time: (Math.floor(new Date(bar.timestamp).getTime() / 1000) as UTCTimestamp),
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
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
  }, [bars]);

  if (bars.length === 0) {
    return (
      <p className="text-sm text-foreground-muted">
        No bar data available for this ticker yet.
      </p>
    );
  }

  return <div ref={containerRef} className="w-full" />;
}
