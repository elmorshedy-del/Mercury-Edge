import { NextResponse } from "next/server";
import { hasDatabase, query } from "@/lib/db";

export const dynamic = "force-dynamic";

const STATIONS = [
  { stid: "KNYC", city: "NYC", name: "Central Park", lat: 40.7789, lon: -73.9692, timezone: "America/New_York" },
  { stid: "KPHL", city: "PHL", name: "Philadelphia", lat: 39.8721, lon: -75.2411, timezone: "America/New_York" },
  { stid: "KLAX", city: "LA", name: "Los Angeles", lat: 33.9425, lon: -118.4081, timezone: "America/Los_Angeles" },
  { stid: "KDEN", city: "Denver", name: "Denver", lat: 39.8561, lon: -104.6737, timezone: "America/Denver" },
  { stid: "KSEA", city: "Seattle", name: "Seattle", lat: 47.4447, lon: -122.3136, timezone: "America/Los_Angeles" },
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

const NWS_HEADERS = {
  "User-Agent": "Mercury-Edge weather dashboard (weather trajectory research)",
  Accept: "application/geo+json",
};

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

type ForecastPoint = { time: string; temp: number };
type ForecastBaseline = {
  localDate: string;
  issuedAt: string | null;
  capturedAt: string;
  points: ForecastPoint[];
};

type BaselineDbRow = {
  local_date: string;
  issued_at: Date | null;
  captured_at: Date;
  points: ForecastPoint[];
};

declare global {
  var weatherBaselineMemory: Map<string, ForecastBaseline> | undefined;
  var weatherPointsMemory: Map<string, { forecastHourly: string; expires: number }> | undefined;
  var weatherBaselineTableReady: Promise<void> | undefined;
}

const baselineMemory = global.weatherBaselineMemory ?? new Map<string, ForecastBaseline>();
const pointsMemory = global.weatherPointsMemory ?? new Map<string, { forecastHourly: string; expires: number }>();
if (!global.weatherBaselineMemory) global.weatherBaselineMemory = baselineMemory;
if (!global.weatherPointsMemory) global.weatherPointsMemory = pointsMemory;

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
  const official = rows.filter((row) => row.kind === "official").slice(-18).reverse();
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

function localDate(timezone: string) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

async function ensureBaselineTable() {
  if (!hasDatabase) return;
  if (!global.weatherBaselineTableReady) {
    global.weatherBaselineTableReady = (async () => {
      await query(`
        CREATE TABLE IF NOT EXISTS weather_trajectory_baselines (
          stid text NOT NULL,
          local_date date NOT NULL,
          timezone text NOT NULL,
          issued_at timestamptz,
          captured_at timestamptz NOT NULL DEFAULT now(),
          points jsonb NOT NULL,
          PRIMARY KEY (stid, local_date)
        )
      `);
    })();
  }
  await global.weatherBaselineTableReady;
}

async function forecastHourlyUrl(config: (typeof STATIONS)[number]) {
  const cached = pointsMemory.get(config.stid);
  if (cached && cached.expires > Date.now()) return cached.forecastHourly;

  const response = await fetch(`https://api.weather.gov/points/${config.lat},${config.lon}`, {
    headers: NWS_HEADERS,
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`NWS points request failed for ${config.stid}`);
  const payload = await response.json();
  const url = payload?.properties?.forecastHourly;
  if (typeof url !== "string") throw new Error(`NWS hourly forecast URL missing for ${config.stid}`);
  pointsMemory.set(config.stid, { forecastHourly: url, expires: Date.now() + 7 * 24 * 60 * 60 * 1000 });
  return url;
}

async function fetchCurrentForecast(config: (typeof STATIONS)[number]): Promise<ForecastBaseline> {
  const url = await forecastHourlyUrl(config);
  const response = await fetch(url, { headers: NWS_HEADERS, cache: "no-store" });
  if (!response.ok) throw new Error(`NWS hourly forecast failed for ${config.stid}`);
  const payload = await response.json();
  const periods = Array.isArray(payload?.properties?.periods) ? payload.properties.periods : [];
  const date = localDate(config.timezone);
  const points: ForecastPoint[] = periods
    .map((period: AnyRecord) => {
      const time = str(period.startTime);
      const rawTemp = num(period.temperature);
      const unit = str(period.temperatureUnit);
      if (!time || rawTemp === null) return null;
      const pointDate = new Intl.DateTimeFormat("en-CA", {
        timeZone: config.timezone,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
      }).format(new Date(time));
      if (pointDate !== date) return null;
      const temp = unit === "C" ? rawTemp * 9 / 5 + 32 : rawTemp;
      return { time, temp };
    })
    .filter((point: ForecastPoint | null): point is ForecastPoint => point !== null);

  return {
    localDate: date,
    issuedAt: str(payload?.properties?.updateTime),
    capturedAt: new Date().toISOString(),
    points,
  };
}

async function getDailyBaseline(config: (typeof STATIONS)[number]): Promise<ForecastBaseline | null> {
  const date = localDate(config.timezone);
  const cacheKey = `${config.stid}:${date}`;
  const inMemory = baselineMemory.get(cacheKey);
  if (inMemory) return inMemory;

  try {
    await ensureBaselineTable();
    if (hasDatabase) {
      const existing = await query<BaselineDbRow>(
        `SELECT local_date::text, issued_at, captured_at, points
         FROM weather_trajectory_baselines
         WHERE stid = $1 AND local_date = $2::date`,
        [config.stid, date],
      );
      if (existing.rows[0]) {
        const row = existing.rows[0];
        const baseline: ForecastBaseline = {
          localDate: row.local_date,
          issuedAt: row.issued_at ? row.issued_at.toISOString() : null,
          capturedAt: row.captured_at.toISOString(),
          points: row.points,
        };
        baselineMemory.set(cacheKey, baseline);
        return baseline;
      }
    }

    const fresh = await fetchCurrentForecast(config);
    if (!fresh.points.length) return null;

    if (hasDatabase) {
      await query(
        `INSERT INTO weather_trajectory_baselines (stid, local_date, timezone, issued_at, captured_at, points)
         VALUES ($1, $2::date, $3, $4::timestamptz, $5::timestamptz, $6::jsonb)
         ON CONFLICT (stid, local_date) DO NOTHING`,
        [config.stid, fresh.localDate, config.timezone, fresh.issuedAt, fresh.capturedAt, JSON.stringify(fresh.points)],
      );
    }

    baselineMemory.set(cacheKey, fresh);
    return fresh;
  } catch (error) {
    console.error(`Unable to build NWS baseline for ${config.stid}`, error);
    return null;
  }
}

export async function GET() {
  const token = process.env.SYNOPTIC_TOKEN;
  if (!token) {
    return NextResponse.json({ error: "SYNOPTIC_TOKEN is not configured" }, { status: 500 });
  }

  const url = new URL("https://api.synopticdata.com/v2/stations/timeseries");
  url.searchParams.set("stid", STATIONS.map((s) => s.stid).join(","));
  url.searchParams.set("recent", "1080");
  url.searchParams.set("vars", VARS);
  url.searchParams.set("units", "english");
  url.searchParams.set("obtimezone", "local");
  url.searchParams.set("hfmetars", "1");
  url.searchParams.set("qc", "on");
  url.searchParams.set("qc_flags", "off");
  url.searchParams.set("token", token);

  try {
    const [response, baselines] = await Promise.all([
      fetch(url, { cache: "no-store" }),
      Promise.all(STATIONS.map((config) => getDailyBaseline(config))),
    ]);
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

    const stations = STATIONS.map((config, index) => {
      const raw = byId.get(config.stid);
      const normalized = raw
        ? { ...config, ...normalizeStation(raw) }
        : { ...config, latest: null, hf: [], official: [], sixHour: [], daily: [], hfAvailable: false };
      return { ...normalized, forecastBaseline: baselines[index] };
    });

    return NextResponse.json(
      { updatedAt: new Date().toISOString(), stations },
      { headers: { "Cache-Control": "no-store, max-age=0" } },
    );
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Unable to fetch weather data" },
      { status: 502 },
    );
  }
}
