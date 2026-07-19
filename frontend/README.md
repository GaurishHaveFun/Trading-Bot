Read-only dashboard for the stock screener (Phase 1 backend under `../backend`). Built with Next.js 16 (App Router), Tailwind CSS, Auth.js v5, the Neon serverless Postgres driver, and TradingView `lightweight-charts`.

## Getting Started

```bash
npm install
cp .env.local.example .env.local   # fill in DATABASE_URL, AUTH_SECRET, DASHBOARD_USER, DASHBOARD_PASSWORD_HASH
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). You'll be redirected to `/login` — sign in with `DASHBOARD_USER` / the plaintext password matching `DASHBOARD_PASSWORD_HASH`.

## Environment variables

See `.env.local.example` for the full list. Notably:

- `DATABASE_URL` — Neon Postgres connection string. The app is read-only against this DB; the write path lives in the Python backend.
- `AUTH_SECRET` — signs/encrypts the JWT session. Generate with `npx auth secret` or `openssl rand -base64 33`.
- `DASHBOARD_USER` / `DASHBOARD_PASSWORD_HASH` — the single hardcoded login. Generate the password hash locally with:

  ```bash
  node -e "console.log(require('bcryptjs').hashSync('yourpassword', 10))"
  ```

  Put the plaintext username in `DASHBOARD_USER` and the resulting hash string in `DASHBOARD_PASSWORD_HASH`. Never commit the plaintext password or `.env.local`.

  **Gotcha (verified while building this):** Next.js's built-in env loader performs `dotenv-expand`-style `${VAR}` interpolation on `.env*` files. A raw bcrypt hash like `$2b$10$Lpbq...` contains several unescaped `$` segments that get silently parsed as variable references and stripped, corrupting the hash so every login fails with no useful error. Escape every `$` as `\$` in `.env.local`:

  ```
  DASHBOARD_PASSWORD_HASH=\$2b\$10\$Lpbq4GMW4K8rlah1Br6I/.eDwMMWbLIH.PXA2niVKCaWaJwhLmITK
  ```

## Notes on this Next.js version

This project was scaffolded on **Next.js 16**, which renamed `middleware.ts` to `proxy.ts` (see `proxy.ts` at the project root) and made `params`/`searchParams` async everywhere. If you're used to older Next.js conventions, check `node_modules/next/dist/docs/01-app/02-guides/upgrading/version-16.md` before assuming APIs match your training data.

## Project layout

- `app/(dashboard)/` — auth-protected routes (latest run, run history, ticker history, backtests).
- `app/login/` — the sign-in page (outside the protected route group).
- `app/api/auth/[...nextauth]/` — Auth.js route handlers.
- `lib/db.ts` — the shared Neon `sql` tagged-template client (lazily initialized so builds don't fail without `DATABASE_URL`).
- `lib/queries.ts` — all read queries against the screener's Postgres schema.
- `lib/types.ts` — TypeScript types mirroring that schema.
- `components/` — `SignalTable`, `RuleBreakdown`, `PriceChart`, `BacktestStats`.
- `proxy.ts` — route protection (Next.js 16's `middleware.ts` replacement).
- `auth.ts` — Auth.js v5 config (Credentials provider, JWT sessions).

## Scripts

```bash
npm run dev      # start dev server
npm run build    # production build
npm run lint     # eslint
npx tsc --noEmit # typecheck
```
