import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const STATIONS = [
  { stid: "KNYC", city: "NYC", timezone: "America/New_York" },
  { stid: "KPHL", city: "PHL", timezone: "America/New_York" },
  { stid: "KLAX", city: "LA", timezone: "America/Los_Angeles" },
  { stid: "KDEN", city: "Denver", timezone: "America/Denver" },
  { stid: "KSEA", city: "Seattle", timezone: "America/Los_Angeles" },
] as const;

const VARS = [
  "air_temp",
  "dew_point_temperature",
  "relative_humidity",
  "wind_speed",
  "wind_direction",
  "altimeter",
  "metar",
].join(",");

type AnyRecord = Record<string, unknown>;

type HfRow = {
  time: string;
  temp: number | null;
  dewPoint: number | null;
  rh: number | null;
  windSpeed: number | null;
  windDirection: number | null;
  altimeter: number | null;
  raw: string | null;
};

function keyFor(obs: AnyRecord, prefix: string) {
  return Object.keys(obs).find((key) => key.startsWith(prefix)) ?? null;
}

function valueAt(obs: AnyRecord, key: string | null, index: number): unknown {
  if (!key) return null;
  const value = obs[key];
  return Array.isArray(value) ? value[index] ?? null : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value.length ? value : null;
}

function localDate(timezone: string, date = new Date()) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function isHf(raw: string | null) {
  if (!raw) return false;
  // Synoptic HFMETAR/MADIS rows lack the routine AO2 remarks carried by official METARs.
  return /^METAR\s+K[A-Z]{3}\s/.test(raw) && !raw.includes(" RMK AO2");
}

function normalizeStation(station: AnyRecord, timezone: string): HfRow[] {
  const obs = (station.OBSERVATIONS ?? {}) as AnyRecord;
  const dates = Array.isArray(obs.date_time) ? (obs.date_time as unknown[]) : [];
  const keys = {
    temp: keyFor(obs, "air_temp_set_"),
    dew: keyFor(obs, "dew_point_temperature_set_"),
    rh: keyFor(obs, "relative_humidity_set_"),
    windSpeed: keyFor(obs, "wind_speed_set_"),
    windDirection: keyFor(obs, "wind_direction_set_"),
    altimeter: keyFor(obs, "altimeter_set_"),
    metar: keyFor(obs, "metar_set_"),
  };
  const today = localDate(timezone);

  return dates
    .map((date, index): HfRow | null => {
      const time = str(date);
      if (!time) return null;
      const raw = str(valueAt(obs, keys.metar, index));
      if (!isHf(raw)) return null;
      if (localDate(timezone, new Date(time)) !== today) return null;
      return {
        time,
        temp: num(valueAt(obs, keys.temp, index)),
        dewPoint: num(valueAt(obs, keys.dew, index)),
        rh: num(valueAt(obs, keys.rh, index)),
        windSpeed: num(valueAt(obs, keys.windSpeed, index)),
        windDirection: num(valueAt(obs, keys.windDirection, index)),
        altimeter: num(valueAt(obs, keys.altimeter, index)),
        raw,
      };
    })
    .filter((row): row is HfRow => row !== null)
    .sort((a, b) => new Date(b.time).getTime() - new Date(a.time).getTime());
}

export async function GET() {
  const token = process.env.SYNOPTIC_TOKEN;
  if (!token) return NextResponse.json({ error: "SYNOPTIC_TOKEN is not configured" }, { status: 500 });

  const url = new URL("https://api.synopticdata.com/v2/stations/timeseries");
  url.searchParams.set("stid", STATIONS.map((station) => station.stid).join(","));
  // 26 hours guarantees local midnight remains inside the query through the entire day,
  // including DST edge cases. We then explicitly filter each station to its current local date.
  url.searchParams.set("recent", "1560");
  url.searchParams.set("vars", VARS);
  url.searchParams.set("units", "english");
  url.searchParams.set("obtimezone", "local");
  url.searchParams.set("hfmetars", "1");
  url.searchParams.set("qc", "on");
  url.searchParams.set("qc_flags", "off");
  url.searchParams.set("token", token);

  try {
    const response = await fetch(url, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok || payload?.SUMMARY?.RESPONSE_CODE !== 1) {
      return NextResponse.json({ error: payload?.SUMMARY?.RESPONSE_MESSAGE ?? "Synoptic request failed" }, { status: 502 });
    }

    const byId = new Map<string, AnyRecord>((payload.STATION ?? []).map((station: AnyRecord) => [String(station.STID), station]));
    const stations = STATIONS.map((config) => {
      const rawStation = byId.get(config.stid);
      const rows = rawStation ? normalizeStation(rawStation, config.timezone) : [];
      return {
        stid: config.stid,
        city: config.city,
        timezone: config.timezone,
        localDate: localDate(config.timezone),
        count: rows.length,
        rows,
      };
    });

    return NextResponse.json(
      { updatedAt: new Date().toISOString(), stations },
      { headers: { "Cache-Control": "no-store, max-age=0, must-revalidate" } },
    );
  } catch (error) {
    console.error("Full-day HF-ASOS request failed", error);
    return NextResponse.json({ error: "Unable to load full-day HF-ASOS history" }, { status: 502 });
  }
}
