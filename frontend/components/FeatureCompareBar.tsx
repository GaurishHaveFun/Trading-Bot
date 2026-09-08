/**
 * One row of the Patterns "Feature comparison" panel: a label plus two
 * horizontal bars — the mean value among the user's model picks vs. the
 * mean among the rest of the universe — scaled to the same maxAbsValue so
 * bar lengths are comparable across both rows of a feature.
 *
 * Per the dataviz skill: identity is carried by the text labels ("Your
 * picks" / "Rest of universe"), not color alone, and the two series use
 * this app's existing --gain (picks) / neutral (rest) tokens rather than a
 * new palette.
 */
export default function FeatureCompareBar({
  label,
  meanPicked,
  meanRest,
  maxAbsValue,
  sampleSize,
}: {
  label: string;
  meanPicked: number;
  meanRest: number;
  maxAbsValue: number;
  sampleSize: number;
}) {
  const pickedWidth = Math.min(100, (Math.abs(meanPicked) / maxAbsValue) * 100);
  const restWidth = Math.min(100, (Math.abs(meanRest) / maxAbsValue) * 100);

  return (
    <div className="flex flex-col gap-1.5 py-2">
      <div className="flex items-baseline justify-between">
        <span className="text-sm font-medium text-foreground">{label}</span>
        <span className="text-[11px] text-foreground-muted">n={sampleSize}</span>
      </div>

      <div className="flex items-center gap-2">
        <span className="w-28 shrink-0 text-[11px] text-foreground-muted">Your picks</span>
        <div className="h-2 flex-1 overflow-hidden rounded-full bg-white/5">
          <div
            className="h-full rounded-full"
            style={{ width: `${pickedWidth}%`, background: "var(--gain)" }}
          />
        </div>
        <span className="w-16 shrink-0 text-right font-mono text-xs tabular-nums text-foreground">
          {meanPicked.toFixed(2)}
        </span>
      </div>

      <div className="flex items-center gap-2">
        <span className="w-28 shrink-0 text-[11px] text-foreground-muted">Rest of universe</span>
        <div className="h-2 flex-1 overflow-hidden rounded-full bg-white/5">
          <div
            className="h-full rounded-full bg-white/25"
            style={{ width: `${restWidth}%` }}
          />
        </div>
        <span className="w-16 shrink-0 text-right font-mono text-xs tabular-nums text-foreground-muted">
          {meanRest.toFixed(2)}
        </span>
      </div>
    </div>
  );
}
