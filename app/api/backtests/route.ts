import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { executeBacktest } from "@/lib/backtest/run";

export const maxDuration = 60;

function authorized(request: NextRequest) {
  const token = process.env.INGEST_TOKEN;
  return Boolean(token && request.headers.get("authorization") === `Bearer ${token}`);
}

export async function GET() {
  if (!pool) return NextResponse.json({ mode: "verified_demo", runs: [] });
  const result = await pool.query(
    `SELECT id, name, model_version, started_at, finished_at, as_of_start,
            as_of_end, status, summary FROM backtest_runs
      ORDER BY started_at DESC LIMIT 20`,
  );
  return NextResponse.json({ mode: "database", runs: result.rows });
}

export async function POST(request: NextRequest) {
  if (!authorized(request)) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const body = await request.json() as { from?: string; to?: string };
  if (!body.from || !/^\d{4}-\d{2}-\d{2}$/.test(body.from)) {
    return NextResponse.json({ error: "from must be YYYY-MM-DD" }, { status: 400 });
  }
  const summary = await executeBacktest(body.from, body.to ?? body.from);
  return NextResponse.json(summary);
}
