import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";
import bcrypt from "bcryptjs";

/**
 * Auth.js v5 (next-auth@beta) configuration.
 *
 * Single hardcoded dashboard user, validated against env vars:
 *   - DASHBOARD_USER: plain-text username, compared with a strict string match.
 *   - DASHBOARD_PASSWORD_HASH: a bcrypt hash, compared with bcryptjs.compare.
 *
 * To generate DASHBOARD_PASSWORD_HASH locally, run:
 *
 *   node -e "console.log(require('bcryptjs').hashSync('yourpassword', 10))"
 *
 * and put the resulting string in frontend/.env.local as DASHBOARD_PASSWORD_HASH.
 *
 * IMPORTANT: Next.js's env loader interpolates `${VAR}`-style references in
 * .env* files, which mangles a raw bcrypt hash's `$2b$10$...` segments.
 * Escape every `$` as `\$` in .env.local (see .env.local.example / README).
 *
 * Session strategy is JWT — there is no database adapter/session table, this
 * app is read-only against the screener's Postgres DB.
 */
export const { handlers, signIn, signOut, auth } = NextAuth({
  session: { strategy: "jwt" },
  pages: {
    signIn: "/login",
  },
  providers: [
    Credentials({
      credentials: {
        username: { label: "Username", type: "text" },
        password: { label: "Password", type: "password" },
      },
      authorize: async (credentials) => {
        const username = credentials?.username;
        const password = credentials?.password;

        if (typeof username !== "string" || typeof password !== "string") {
          return null;
        }

        const expectedUser = process.env.DASHBOARD_USER;
        const expectedHash = process.env.DASHBOARD_PASSWORD_HASH;

        if (!expectedUser || !expectedHash) {
          // Auth isn't configured yet — fail closed.
          return null;
        }

        if (username !== expectedUser) {
          return null;
        }

        const passwordMatches = await bcrypt.compare(password, expectedHash);
        if (!passwordMatches) {
          return null;
        }

        return { id: "dashboard-user", name: username };
      },
    }),
  ],
});
