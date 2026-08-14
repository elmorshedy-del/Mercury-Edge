import { NextRequest, NextResponse } from "next/server";
import { authorized } from "@/lib/auth";
import { pool } from "@/lib/db";
import { createJob } from "@/lib/jobs/repository";
import { validateJobParameters } from "@/lib/jobs/validation";

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
  try {
    const body = await request.json() as Record<string, unknown>;
    const parameters = validateJobParameters(body);
    const jobId = await createJob("backtest", parameters);
    return NextResponse.json({ jobId, status: "queued" }, { status: 202 });
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : String(error) }, { status: 400 });
  }
}
