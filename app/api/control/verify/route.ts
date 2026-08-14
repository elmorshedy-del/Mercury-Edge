import { NextRequest, NextResponse } from "next/server";
import { authorized } from "@/lib/auth";

export async function POST(request: NextRequest) {
  if (!authorized(request)) return NextResponse.json({ ok: false }, { status: 401 });
  return NextResponse.json({ ok: true });
}
