"use client";

import { signOut } from "next-auth/react";

export default function SignOutButton() {
  return (
    <button
      onClick={() => signOut({ callbackUrl: "/login" })}
      className="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-xs font-medium text-foreground-muted transition-colors hover:border-white/20 hover:bg-white/10 hover:text-foreground"
    >
      Sign out
    </button>
  );
}
