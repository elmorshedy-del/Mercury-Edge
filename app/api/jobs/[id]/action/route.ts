import { NextRequest, NextResponse } from "next/server";
import { authorized } from "@/lib/auth";
import { requestJobAction } from "@/lib/jobs/repository";
import type { JobControlAction } from "@/lib/jobs/types";

const ACTIONS = new Set<JobControlAction>(["pause", "resume", "cancel", "retry"]);

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ id: string }> },
) {
  if (!authorized(request)) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const { id } = await context.params;
  const body = await request.json().catch(() => ({})) as { action?: JobControlAction };
  if (!body.action || !ACTIONS.has(body.action)) {
    return NextResponse.json({ error: "Invalid action" }, { status: 400 });
  }
  try {
    const status = await requestJobAction(id, body.action);
    if (!status) return NextResponse.json({ error: "Job not found" }, { status: 404 });
    return NextResponse.json({ id, status });
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : String(error) }, { status: 409 });
  }
}
