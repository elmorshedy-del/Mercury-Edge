import { NextResponse } from "next/server";
import { getJobDetail } from "@/lib/jobs/repository";

export const dynamic = "force-dynamic";

export async function GET(
  _request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const { id } = await context.params;
  const detail = await getJobDetail(id);
  if (!detail) return NextResponse.json({ error: "Job not found" }, { status: 404 });
  return NextResponse.json(detail);
}
