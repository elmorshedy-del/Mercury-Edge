import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

type Station = { stid: string; lat: number; lon: number; timezone: string };
const STATIONS: Station[] = [
  { stid: "KNYC", lat: 40.7789, lon: -73.9692, timezone: "America/New_York" },
  { stid: "KPHL", lat: 39.8721, lon: -75.2411, timezone: "America/New_York" },
  { stid: "KLAX", lat: 33.9425, lon: -118.4081, timezone: "America/Los_Angeles" },
  { stid: "KDEN", lat: 39.8561, lon: -104.6737, timezone: "America/Denver" },
  { stid: "KSEA", lat: 47.4447, lon: -122.3136, timezone: "America/Los_Angeles" },
];

type ModelPoint = {
  time: string;
  temp: number | null;
  dewPoint: number | null;
  cloudCover: number | null;
  windSpeed: number | null;
  windDirection: number | null;
  precipitation: number | null;
  shortwaveRadiation: number | null;
};

function valueAt(values: unknown, index: number) {
  if (!Array.isArray(values)) return null;
  const value = values[index];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

async function fetchModel(station: Station, model: "ncep_hrrr_conus" | "ncep_nbm_conus") {
  const url = new URL("https://api.open-meteo.com/v1/gfs");
  url.searchParams.set("latitude", String(station.lat));
  url.searchParams.set("longitude", String(station.lon));
  url.searchParams.set("models", model);
  url.searchParams.set("hourly", [
    "temperature_2m",
    "dew_point_2m",
    "cloud_cover",
    "wind_speed_10m",
    "wind_direction_10m",
    "precipitation",
    "shortwave_radiation",
  ].join(","));
  url.searchParams.set("temperature_unit", "fahrenheit");
  url.searchParams.set("wind_speed_unit", "mph");
  url.searchParams.set("timezone", station.timezone);
  url.searchParams.set("past_days", "1");
  url.searchParams.set("forecast_days", "2");

  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`${model} request failed (${response.status})`);
  const payload = await response.json();
  const hourly = payload?.hourly ?? {};
  const times = Array.isArray(hourly.time) ? hourly.time : [];
  const points: ModelPoint[] = times.map((time: unknown, index: number) => ({
    time: String(time),
    temp: valueAt(hourly.temperature_2m, index),
    dewPoint: valueAt(hourly.dew_point_2m, index),
    cloudCover: valueAt(hourly.cloud_cover, index),
    windSpeed: valueAt(hourly.wind_speed_10m, index),
    windDirection: valueAt(hourly.wind_direction_10m, index),
    precipitation: valueAt(hourly.precipitation, index),
    shortwaveRadiation: valueAt(hourly.shortwave_radiation, index),
  }));

  return {
    model,
    latitude: payload?.latitude ?? station.lat,
    longitude: payload?.longitude ?? station.lon,
    elevation: payload?.elevation ?? null,
    generationTimeMs: payload?.generationtime_ms ?? null,
    points,
  };
}

export async function GET(request: NextRequest) {
  const stid = request.nextUrl.searchParams.get("stid")?.toUpperCase() ?? "KNYC";
  const station = STATIONS.find((item) => item.stid === stid);
  if (!station) return NextResponse.json({ error: `Unsupported station ${stid}` }, { status: 400 });

  try {
    const [hrrr, nbm] = await Promise.all([
      fetchModel(station, "ncep_hrrr_conus"),
      fetchModel(station, "ncep_nbm_conus"),
    ]);
    return NextResponse.json({
      stid,
      timezone: station.timezone,
      source: "Open-Meteo pass-through of NOAA HRRR/NBM",
      role: "explanatory-only",
      hrrr,
      nbm,
      updatedAt: new Date().toISOString(),
    }, { headers: { "Cache-Control": "public, max-age=120, stale-while-revalidate=300" } });
  } catch (error) {
    return NextResponse.json({
      error: error instanceof Error ? error.message : "Unable to load HRRR/NBM context",
    }, { status: 502 });
  }
}
