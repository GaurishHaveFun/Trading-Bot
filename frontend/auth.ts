import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";
import bcrypt from "bcryptjs";

/**
 * Auth.js v5 (next-auth@beta) configuration.
 *
 * Up to two hardcoded dashboard users, each validated against its own pair
 * of env vars:
 *   - DASHBOARD_USER / DASHBOARD_USER_2: plain-text username, compared with
 *     a strict string match.
 *   - DASHBOARD_PASSWORD_HASH / DASHBOARD_PASSWORD_HASH_2: a bcrypt hash,
 *     compared with bcryptjs.compare.
 *
 * The second user is optional — if either DASHBOARD_USER_2 or
 * DASHBOARD_PASSWORD_HASH_2 is unset, that branch simply never matches.
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

        if (expectedUser && expectedHash && username === expectedUser) {
          const passwordMatches = await bcrypt.compare(password, expectedHash);
          if (passwordMatches) {
            return { id: "dashboard-user", name: username };
          }
        }

        const expectedUser2 = process.env.DASHBOARD_USER_2;
        const expectedHash2 = process.env.DASHBOARD_PASSWORD_HASH_2;

        if (expectedUser2 && expectedHash2 && username === expectedUser2) {
          const passwordMatches2 = await bcrypt.compare(password, expectedHash2);
          if (passwordMatches2) {
            return { id: "dashboard-user-2", name: username };
          }
        }

        return null;
      },
    }),
  ],
});
