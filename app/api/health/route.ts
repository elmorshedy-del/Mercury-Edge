import { NextResponse } from "next/server";
import { hasDatabase, pool } from "@/lib/db";

export const dynamic = "force-dynamic";

export async function GET() {
  let database: "connected" | "demo" | "error" = hasDatabase ? "connected" : "demo";
  if (pool) {
    try {
      await pool.query("SELECT 1");
    } catch {
      database = "error";
    }
  }
  return NextResponse.json(
    { ok: database !== "error", service: "mercury-edge", database, at: new Date().toISOString() },
    { status: database === "error" ? 503 : 200 },
  );
}
