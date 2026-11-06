"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useSyncExternalStore } from "react";

type NavEntry = { url?: string | null };
type NavApi = { currentEntry?: { index: number } | null; entries(): NavEntry[] };

/**
 * True when the previous history entry is a same-origin page, i.e. router.back()
 * stays inside the app. Prefers the Navigation API (exact previous entry);
 * falls back to history.length + same-origin document.referrer (note: the
 * referrer only describes the entry the tab first loaded, so this is a
 * best-effort heuristic on browsers without the Navigation API).
 */
export function hasInAppHistory(): boolean {
  if (typeof window === "undefined") return false;
  const nav = (window as unknown as { navigation?: NavApi }).navigation;
  if (nav?.currentEntry) {
    const prev = nav.entries()[nav.currentEntry.index - 1];
    if (!prev?.url) return false;
    try {
      return new URL(prev.url).origin === window.location.origin;
    } catch {
      return false;
    }
  }
  if (window.history.length <= 1) return false;
  try {
    return (
      document.referrer !== "" &&
      new URL(document.referrer).origin === window.location.origin
    );
  } catch {
    return false;
  }
}

const subscribe = () => () => {};

/**
 * Back button for detail pages. With in-app history it calls router.back(),
 * which Next restores from its client router cache (no refetch, scroll kept).
 * Otherwise it renders a plain link to the fallback route.
 */
export default function BackButton({
  fallbackHref = "/watchlist",
  fallbackLabel = "Watchlist",
}: {
  fallbackHref?: string;
  fallbackLabel?: string;
}) {
  const router = useRouter();
  // Server snapshot is false so SSR and hydration render the Link; the client
  // then switches to the button if in-app history exists.
  const canGoBack = useSyncExternalStore(subscribe, hasInAppHistory, () => false);

  const className =
    "inline-flex w-fit items-center gap-1.5 rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-xs font-medium text-foreground-muted transition-colors hover:border-white/20 hover:bg-white/10 hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white/40";

  const icon = (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-3.5 w-3.5"
      aria-hidden="true"
    >
      <path d="M19 12H5" />
      <path d="m12 19-7-7 7-7" />
    </svg>
  );

  if (canGoBack) {
    return (
      <button type="button" onClick={() => router.back()} className={className}>
        {icon}
        Back
      </button>
    );
  }

  return (
    <Link href={fallbackHref} className={className} aria-label={`Back to ${fallbackLabel}`}>
      {icon}
      Back
    </Link>
  );
}
