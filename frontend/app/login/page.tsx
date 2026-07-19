"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { signIn } from "next-auth/react";

export default function LoginPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setLoading(true);

    const formData = new FormData(event.currentTarget);
    const username = formData.get("username");
    const password = formData.get("password");

    try {
      const result = await signIn("credentials", {
        username,
        password,
        redirect: false,
      });

      if (!result || result.error) {
        setError("Invalid username or password.");
        setLoading(false);
        return;
      }

      router.push("/");
      router.refresh();
    } catch {
      setError("Something went wrong signing in. Please try again.");
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen flex-1 items-center justify-center px-4">
      <div className="glass-panel-dense panel-enter w-full max-w-sm p-7">
        <p className="font-mono text-xs uppercase tracking-widest text-gradient-accent">
          Screener
        </p>
        <h1 className="mt-2 font-display text-2xl font-semibold text-foreground">
          Sign in
        </h1>
        <p className="mt-1 text-sm text-foreground-muted">
          View screener runs, signals, and backtests.
        </p>

        <form onSubmit={handleSubmit} className="mt-6 flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="username" className="text-sm font-medium text-foreground-muted">
              Username
            </label>
            <input
              id="username"
              name="username"
              type="text"
              autoComplete="username"
              required
              className="rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm text-foreground outline-none transition-colors focus-visible:border-accent"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label htmlFor="password" className="text-sm font-medium text-foreground-muted">
              Password
            </label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              className="rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm text-foreground outline-none transition-colors focus-visible:border-accent"
            />
          </div>

          {error && (
            <p className="text-sm" style={{ color: "var(--loss)" }} role="alert">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="glass-interactive mt-2 rounded-xl bg-gradient-accent px-3 py-2.5 text-sm font-semibold text-[#0a0b14] transition-opacity hover:opacity-90 disabled:opacity-60"
          >
            {loading ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
