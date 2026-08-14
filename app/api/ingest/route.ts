import { NextRequest, NextResponse } from "next/server";
import { STATIONS } from "@/lib/config";
import { ingestAll } from "@/lib/ingest";

export const maxDuration = 60;

function authorized(request: NextRequest) {
  const token = process.env.INGEST_TOKEN;
  return Boolean(token && request.headers.get("authorization") === `Bearer ${token}`);
}

export async function POST(request: NextRequest) {
  if (!authorized(request)) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const body = await request.json().catch(() => ({})) as { stations?: string[] };
  const wanted = body.stations?.length ? new Set(body.stations.map((item) => item.toUpperCase())) : null;
  const stations = wanted ? STATIONS.filter((station) => wanted.has(station.station)) : STATIONS;
  const results = await ingestAll(new Date(), stations);
  return NextResponse.json({ results });
}
