import { NextResponse } from "next/server";
import { auth } from "@/auth";

/**
 * Route protection for the dashboard.
 *
 * NOTE: Next.js 16 renamed the `middleware.ts` file convention to
 * `proxy.ts` (the exported function is now `proxy`, not `middleware`).
 * See https://nextjs.org/docs/app/api-reference/file-conventions/proxy
 * — `middleware.ts` is deprecated in this Next.js version.
 *
 * Everything is protected except `/login` and Next's internal/static
 * assets, which are already excluded via the matcher below.
 */
export default auth((req) => {
  // IMPORTANT: don't check `Boolean(req.auth)` — when Auth.js hits an
  // internal configuration error (e.g. a missing/invalid AUTH_SECRET), it
  // does not throw or leave `req.auth` null. Instead it swallows the error,
  // logs it, and sets `req.auth` to a truthy error object shaped like
  // `{ message: "There was a problem with the server configuration..." }`.
  // `Boolean(req.auth)` on that object is `true`, which would fail OPEN and
  // serve protected pages to unauthenticated requests whenever auth is
  // misconfigured. A real session always has a `user` field (see the
  // `authorize()`/session callback in auth.ts), so gate on that instead —
  // this fails closed (redirects to /login) both when there's no session
  // and when Auth.js itself is misconfigured.
  const isLoggedIn = Boolean(req.auth?.user);
  const { pathname } = req.nextUrl;

  if (pathname.startsWith("/login")) {
    return NextResponse.next();
  }

  if (!isLoggedIn) {
    const loginUrl = new URL("/login", req.nextUrl);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
});

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"],
};
