import { NextRequest, NextResponse } from "next/server";
import { authorized } from "@/lib/auth";
import { createJob, createPipeline, listJobs } from "@/lib/jobs/repository";
import { validateJobParameters } from "@/lib/jobs/validation";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const limit = Number(request.nextUrl.searchParams.get("limit") ?? 30);
  return NextResponse.json({ jobs: await listJobs(limit), generatedAt: new Date().toISOString() });
}

export async function POST(request: NextRequest) {
  if (!authorized(request)) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  try {
    const body = await request.json() as Record<string, unknown>;
    const parameters = validateJobParameters(body);
    if (body.jobType === "pipeline") {
      const pipeline = await createPipeline(parameters);
      return NextResponse.json(pipeline, { status: 202 });
    }
    if (body.jobType !== "backfill" && body.jobType !== "backtest") {
      return NextResponse.json({ error: "jobType must be backfill, backtest, or pipeline" }, { status: 400 });
    }
    const jobId = await createJob(body.jobType, parameters);
    return NextResponse.json({ jobId }, { status: 202 });
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : String(error) }, { status: 400 });
  }
}
