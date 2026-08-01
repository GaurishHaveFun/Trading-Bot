"use client";

import { useId, useState, useTransition, type FormEvent } from "react";
import { buyStock, sellStock } from "@/lib/paper";

/**
 * Buy/sell entry point calling the paper-trading server actions directly
 * (useTransition + local state, mirroring the RefreshButton pattern already
 * in this codebase) rather than useActionState — buyStock/sellStock take
 * (ticker, quantity), not the (prevState, formData) shape useActionState
 * expects, and there's no shared submit-multiple-actions need here that
 * would justify reshaping them.
 *
 * `side` is fixed per instance: the portfolio page's general panel renders
 * a `side="buy"` ticket with a free-text ticker input, while each holdings
 * row and each watchlist row renders a compact `lockTicker` ticket for that
 * one ticker (sell / buy respectively).
 *
 * The server actions already call revalidatePath("/") on success, so the
 * page will refetch fresh account/position data on its own — this
 * component only owns its own pending/error/success UI, not the resulting
 * portfolio state.
 */
export default function TradeTicket({
  ticker: initialTicker = "",
  lockTicker = false,
  side,
  maxQuantity,
  compact = false,
}: {
  ticker?: string;
  lockTicker?: boolean;
  side: "buy" | "sell";
  maxQuantity?: number;
  compact?: boolean;
}) {
  const formId = useId();
  const [ticker, setTicker] = useState(initialTicker);
  const [quantity, setQuantity] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [isPending, startTransition] = useTransition();

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(false);

    const trimmedTicker = ticker.trim().toUpperCase();
    const qty = Number(quantity);

    if (!trimmedTicker) {
      setError("Enter a ticker");
      return;
    }
    if (!(qty > 0)) {
      setError("Enter a quantity greater than zero");
      return;
    }
    if (maxQuantity !== undefined && qty > maxQuantity) {
      setError(`Only ${maxQuantity} shares held`);
      return;
    }

    startTransition(async () => {
      const action = side === "buy" ? buyStock : sellStock;
      const result = await action(trimmedTicker, qty);
      if (result.success) {
        setSuccess(true);
        setQuantity("");
      } else {
        setError(result.error ?? "Trade failed");
      }
    });
  }

  const actionLabel = side === "buy" ? "Buy" : "Sell";

  return (
    <form
      onSubmit={handleSubmit}
      className={compact ? "flex flex-wrap items-center justify-end gap-2" : "flex flex-wrap items-end gap-3"}
    >
      {!lockTicker && (
        <div className="flex flex-col gap-1">
          <label
            className="text-xs uppercase tracking-wide text-foreground-muted"
            htmlFor={`${formId}-ticker`}
          >
            Ticker
          </label>
          <input
            id={`${formId}-ticker`}
            type="text"
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            placeholder="AAPL"
            className="w-24 rounded-lg border border-white/10 bg-white/5 px-2.5 py-1.5 font-mono text-sm text-foreground placeholder:text-foreground-muted/50 focus:border-white/20 focus:outline-none"
          />
        </div>
      )}
      <div className="flex flex-col gap-1">
        {!compact && (
          <label
            className="text-xs uppercase tracking-wide text-foreground-muted"
            htmlFor={`${formId}-qty`}
          >
            Quantity
          </label>
        )}
        <input
          id={`${formId}-qty`}
          type="number"
          min="0"
          step="any"
          max={maxQuantity}
          value={quantity}
          onChange={(e) => setQuantity(e.target.value)}
          placeholder={compact ? "Qty" : "0"}
          className="w-20 rounded-lg border border-white/10 bg-white/5 px-2.5 py-1.5 font-mono text-sm text-foreground placeholder:text-foreground-muted/50 focus:border-white/20 focus:outline-none"
        />
      </div>
      <button
        type="submit"
        disabled={isPending}
        aria-busy={isPending}
        className={`rounded-lg px-3 py-1.5 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${
          side === "buy"
            ? "bg-gradient-accent text-[#0a0b14] hover:brightness-110"
            : "border border-white/10 bg-white/5 text-foreground hover:bg-white/10"
        }`}
      >
        {isPending ? "…" : actionLabel}
      </button>
      {error && (
        <span className="text-xs" style={{ color: "var(--loss)" }} role="alert">
          {error}
        </span>
      )}
      {success && !error && (
        <span className="text-xs" style={{ color: "var(--gain)" }}>
          {actionLabel === "Buy" ? "Bought" : "Sold"}
        </span>
      )}
    </form>
  );
}
