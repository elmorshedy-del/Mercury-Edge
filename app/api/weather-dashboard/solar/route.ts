import { NextResponse } from "next/server";
import { SOLAR_STATIONS } from "@/lib/weather/solar-model";
import { getSolarReading } from "@/lib/weather/solar-source";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const maxDuration = 120;

export async function GET(request: Request) {
  const stid = new URL(request.url).searchParams.get("station");
  const station = SOLAR_STATIONS.find(s => s.stid === stid);
  if (!station) return NextResponse.json({error:"Unknown station"},{status:400});
  return NextResponse.json(await getSolarReading(station),{headers:{"Cache-Control":"no-store"}});
}
