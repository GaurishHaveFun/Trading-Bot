import Link from "next/link";
import SignOutButton from "@/components/SignOutButton";

export default function DashboardLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="glass-header-bar sticky top-0 z-10 px-4 py-3">
        <div className="mx-auto flex max-w-6xl items-center justify-between">
          <nav className="flex items-center gap-6 text-sm font-medium">
            <Link href="/" className="font-display text-base font-semibold text-foreground">
              Ledger
            </Link>
            <Link
              href="/"
              className="text-foreground-muted transition-colors hover:text-foreground"
            >
              Portfolio
            </Link>
            <Link
              href="/watchlist"
              className="text-foreground-muted transition-colors hover:text-foreground"
            >
              Watchlist
            </Link>
            <Link
              href="/screener"
              className="text-foreground-muted transition-colors hover:text-foreground"
            >
              Screener
            </Link>
          </nav>
          <SignOutButton />
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">{children}</main>
    </div>
  );
}
