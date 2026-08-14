import { NextResponse } from "next/server";
import { getDashboard } from "@/lib/dashboard";

export const dynamic = "force-dynamic";

export async function GET() {
  return NextResponse.json(await getDashboard(), {
    headers: { "Cache-Control": "public, max-age=30, stale-while-revalidate=120" },
  });
}
