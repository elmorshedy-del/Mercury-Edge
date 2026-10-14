import { NextResponse } from 'next/server';
import { SOLAR_STATIONS } from '@/lib/weather/solar-model';
import { getSolarForecast } from '@/lib/weather/solar-forecast-source';
export const dynamic='force-dynamic';
export const runtime='nodejs';
export async function GET(request: Request) {
  const station=SOLAR_STATIONS.find(s=>s.stid===new URL(request.url).searchParams.get('station'));
  if (!station) return NextResponse.json({error:'Unknown station'},{status:400});
  return NextResponse.json(getSolarForecast(station),{headers:{'Cache-Control':'no-store'}});
}
