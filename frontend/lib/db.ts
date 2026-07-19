import { neon, type NeonQueryFunction } from "@neondatabase/serverless";

/**
 * Shared Neon HTTP driver client, used as a plain tagged-template SQL
 * client everywhere (see lib/queries.ts). No ORM.
 *
 * IMPORTANT: `neon()` validates its connection string argument eagerly, so
 * we must not call it at module-eval time with an empty/missing
 * DATABASE_URL — that would throw during build (page prerendering / route
 * collection) even on pages that never actually touch the DB. Instead we
 * lazily construct the client on first use, and fall back to an obviously
 * invalid placeholder connection string when DATABASE_URL isn't set so
 * `neon()` itself never throws at import time. Callers only see an error
 * once a query actually executes at request time, which is the desired
 * behavior when no DB is configured yet.
 */

let cachedSql: NeonQueryFunction<false, false> | null = null;

function getSql(): NeonQueryFunction<false, false> {
  if (!cachedSql) {
    const connectionString =
      process.env.DATABASE_URL ||
      "postgresql://user:password@localhost/db_not_configured";
    cachedSql = neon(connectionString);
  }
  return cachedSql;
}

/**
 * Tagged-template SQL client. Usage: `sql\`SELECT * FROM runs WHERE id = ${id}\``
 * Template interpolations are automatically parameterized by the Neon
 * driver — never build queries via string concatenation.
 */
export const sql: NeonQueryFunction<false, false> = ((...args: Parameters<NeonQueryFunction<false, false>>) =>
  getSql()(...args)) as NeonQueryFunction<false, false>;

export const isDatabaseConfigured = (): boolean => Boolean(process.env.DATABASE_URL);
