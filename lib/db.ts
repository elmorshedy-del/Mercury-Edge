import { Pool, type PoolClient, type QueryResultRow } from "pg";

declare global {
  var mercuryPool: Pool | undefined;
}

export const hasDatabase = Boolean(process.env.DATABASE_URL);

export const pool =
  global.mercuryPool ??
  (hasDatabase
    ? new Pool({
        connectionString: process.env.DATABASE_URL,
        ssl: process.env.NODE_ENV === "production" ? { rejectUnauthorized: false } : undefined,
        max: 8,
      })
    : null);

if (process.env.NODE_ENV !== "production" && pool) global.mercuryPool = pool;

export async function query<T extends QueryResultRow>(text: string, values: unknown[] = []) {
  if (!pool) throw new Error("DATABASE_URL is not configured");
  return pool.query<T>(text, values);
}

export async function transaction<T>(fn: (client: PoolClient) => Promise<T>) {
  if (!pool) throw new Error("DATABASE_URL is not configured");
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    const result = await fn(client);
    await client.query("COMMIT");
    return result;
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally {
    client.release();
  }
}
