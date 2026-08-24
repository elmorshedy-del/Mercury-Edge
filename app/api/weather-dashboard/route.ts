import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const STATIONS = [
  { stid: "KNYC", city: "NYC", name: "Central Park" },
  { stid: "KPHL", city: "PHL", name: "Philadelphia" },
  { stid: "KLAX", city: "LA", name: "Los Angeles" },
  { stid: "KDEN", city: "Denver", name: "Denver" },
  { stid: "KSEA", city: "Seattle", name: "Seattle" },
];

const VARS = [
  "air_temp",
  "relative_humidity",
  "wind_speed",
  "wind_direction",
  "altimeter",
  "sea_level_pressure",
  "air_temp_high_6_hour",
  "air_temp_low_6_hour",
  "air_temp_high_24_hour",
  "air_temp_low_24_hour",
  "metar",
].join(",");

type AnyRecord = Record<string, unknown>;

type Row = {
  time: string;
  temp: number | null;
  rh: number | null;
  windSpeed: number | null;
  windDirection: number | null;
  altimeter: number | null;
  seaLevelPressure: number | null;
  high6: number | null;
  low6: number | null;
  high24: number | null;
  low24: number | null;
  raw: string | null;
  kind: "hf" | "official" | "other";
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

function classify(raw: string | null): Row["kind"] {
  if (!raw) return "other";
  if (/^METAR\s+K[A-Z]{3}\s/.test(raw) && !raw.includes(" RMK AO2")) return "hf";
  return "official";
}

function normalizeStation(station: AnyRecord) {
  const obs = (station.OBSERVATIONS ?? {}) as AnyRecord;
  const dates = Array.isArray(obs.date_time) ? (obs.date_time as unknown[]) : [];

  const keys = {
    temp: keyFor(obs, "air_temp_set_"),
    rh: keyFor(obs, "relative_humidity_set_"),
    windSpeed: keyFor(obs, "wind_speed_set_"),
    windDirection: keyFor(obs, "wind_direction_set_"),
    altimeter: keyFor(obs, "altimeter_set_"),
    seaLevelPressure: keyFor(obs, "sea_level_pressure_set_1"),
    high6: keyFor(obs, "air_temp_high_6_hour_set_"),
    low6: keyFor(obs, "air_temp_low_6_hour_set_"),
    high24: keyFor(obs, "air_temp_high_24_hour_set_"),
    low24: keyFor(obs, "air_temp_low_24_hour_set_"),
    metar: keyFor(obs, "metar_set_"),
  };

  const rows: Row[] = dates.map((date, index) => {
    const raw = str(valueAt(obs, keys.metar, index));
    return {
      time: str(date) ?? "",
      temp: num(valueAt(obs, keys.temp, index)),
      rh: num(valueAt(obs, keys.rh, index)),
      windSpeed: num(valueAt(obs, keys.windSpeed, index)),
      windDirection: num(valueAt(obs, keys.windDirection, index)),
      altimeter: num(valueAt(obs, keys.altimeter, index)),
      seaLevelPressure: num(valueAt(obs, keys.seaLevelPressure, index)),
      high6: num(valueAt(obs, keys.high6, index)),
      low6: num(valueAt(obs, keys.low6, index)),
      high24: num(valueAt(obs, keys.high24, index)),
      low24: num(valueAt(obs, keys.low24, index)),
      raw,
      kind: classify(raw),
    };
  });

  const latest = [...rows].reverse().find((row) => row.temp !== null || row.raw) ?? null;
  const hf = rows.filter((row) => row.kind === "hf").slice(-24).reverse();
  const official = rows.filter((row) => row.kind === "official").slice(-12).reverse();
  const sixHour = rows.filter((row) => row.high6 !== null || row.low6 !== null).slice(-6).reverse();
  const daily = rows.filter((row) => row.high24 !== null || row.low24 !== null).slice(-4).reverse();

  return {
    stid: station.STID,
    name: station.NAME,
    timezone: station.TIMEZONE,
    latest,
    hf,
    official,
    sixHour,
    daily,
    hfAvailable: hf.length > 0,
  };
}

export async function GET() {
  const token = process.env.SYNOPTIC_TOKEN;
  if (!token) {
    return NextResponse.json({ error: "SYNOPTIC_TOKEN is not configured" }, { status: 500 });
  }

  const url = new URL("https://api.synopticdata.com/v2/stations/timeseries");
  url.searchParams.set("stid", STATIONS.map((s) => s.stid).join(","));
  url.searchParams.set("recent", "900");
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
      return NextResponse.json(
        { error: payload?.SUMMARY?.RESPONSE_MESSAGE ?? "Synoptic request failed" },
        { status: 502 },
      );
    }

    const byId = new Map<string, AnyRecord>(
      (payload.STATION ?? []).map((station: AnyRecord) => [String(station.STID), station]),
    );

    const stations = STATIONS.map((config) => {
      const raw = byId.get(config.stid);
      return raw
        ? { ...config, ...normalizeStation(raw) }
        : { ...config, timezone: null, latest: null, hf: [], official: [], sixHour: [], daily: [], hfAvailable: false };
    });

    return NextResponse.json({
      updatedAt: new Date().toISOString(),
      stations,
    });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Unable to fetch weather data" },
      { status: 502 },
    );
  }
}
