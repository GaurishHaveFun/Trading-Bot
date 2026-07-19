"use client";

import { useEffect, useRef, useState } from "react";

const SIZE_CLASSES = {
  sm: { track: "h-1.5", text: "text-xs", gap: "gap-2" },
  md: { track: "h-2", text: "text-sm", gap: "gap-3" },
  lg: { track: "h-3.5", text: "text-lg", gap: "gap-4" },
} as const;

/**
 * Horizontal score gauge: fills to `score` (0-1) against a glass track, with
 * a fixed tick mark at `threshold` (0-1). The fill is the cyan->violet
 * accent gradient; crossing the threshold adds a glow/pulse (skipped under
 * prefers-reduced-motion). Fill animates from 0 on mount.
 */
export default function ScoreGauge({
  score,
  threshold,
  size = "md",
}: {
  score: number;
  threshold: number;
  size?: "sm" | "md" | "lg";
}) {
  const clampedScore = Math.min(Math.max(score, 0), 1);
  const clampedThreshold = Math.min(Math.max(threshold, 0), 1);
  const fired = clampedScore >= clampedThreshold;
  const classes = SIZE_CLASSES[size];

  const [width, setWidth] = useState(0);
  const frame = useRef<number | null>(null);

  useEffect(() => {
    // Width starts at 0 (initial state) so the browser has something to
    // transition from; commit the real width on the next frame.
    frame.current = requestAnimationFrame(() => {
      setWidth(clampedScore * 100);
    });
    return () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    };
  }, [clampedScore]);

  return (
    <div className={`flex items-center ${classes.gap}`}>
      <div
        className={`relative flex-1 min-w-16 ${classes.track} overflow-hidden rounded-full border border-white/10 bg-white/5`}
        role="meter"
        aria-valuenow={Math.round(clampedScore * 100)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Score"
      >
        <div
          className={`h-full rounded-full bg-gradient-accent transition-[width] duration-700 ease-out ${
            fired ? "motion-safe:animate-[gauge-pulse_2s_ease-in-out_infinite]" : ""
          }`}
          style={{
            width: `${width}%`,
            boxShadow: fired
              ? "0 0 8px rgba(167, 139, 250, 0.7), 0 0 16px rgba(34, 211, 238, 0.4)"
              : undefined,
          }}
        />
        <div
          className="absolute top-0 bottom-0 w-px bg-foreground-muted/60"
          style={{ left: `${clampedThreshold * 100}%` }}
          aria-hidden="true"
        />
      </div>
      <span className={`font-mono tabular-nums text-foreground ${classes.text}`}>
        {(clampedScore * 100).toFixed(1)}%
      </span>
    </div>
  );
}
