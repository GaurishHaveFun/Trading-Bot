"use client";

import { useState, useTransition } from "react";
import { addToModel, removeFromModel } from "@/lib/model";

/**
 * "I'd buy this" toggle for a ticker detail page. Mirrors RefreshButton's
 * useTransition + local pending/error state pattern rather than
 * useActionState, since addToModel/removeFromModel take a plain ticker
 * argument, not the (prevState, formData) shape useActionState expects.
 *
 * The server actions already call revalidatePath, so this component only
 * owns its own optimistic `inModel` flag + error text, not any wider page
 * state.
 */
export default function ModelToggleButton({
  ticker,
  initialInModel,
}: {
  ticker: string;
  initialInModel: boolean;
}) {
  const [inModel, setInModel] = useState(initialInModel);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function handleClick() {
    startTransition(async () => {
      const result = await (inModel ? removeFromModel(ticker) : addToModel(ticker));
      if (result.success) {
        setInModel(!inModel);
        setError(null);
      } else {
        setError(result.error ?? "Something went wrong");
      }
    });
  }

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={handleClick}
        disabled={isPending}
        aria-busy={isPending}
        className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${
          inModel
            ? "bg-gradient-accent text-[#0a0b14] hover:brightness-110"
            : "border border-white/10 bg-white/5 text-foreground-muted hover:border-white/20 hover:bg-white/10 hover:text-foreground"
        }`}
      >
        {isPending ? "…" : inModel ? "In Model ✓" : "Add to Model"}
      </button>
      {error && (
        <span className="text-xs" style={{ color: "var(--loss)" }} role="alert">
          {error}
        </span>
      )}
    </div>
  );
}
