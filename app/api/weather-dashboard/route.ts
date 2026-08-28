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
  "dew_point_temperature",
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

const USER_AGENT = process.env.SOURCE_USER_AGENT || "Mercury-Edge weather dashboard (weather trajectory research)";
const AWC_HEADERS = { "User-Agent": USER_AGENT, Accept: "application/json" };

type AnyRecord = Record<string, unknown>;

type Row = {
  time: string;
  temp: number | null;
  dewPoint: number | null;
  cloudCover: number | null;
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
  source?: "synoptic" | "awc";
  receivedAt?: string | null;
};

type ForecastPoint = {
  time: string;
  temp: number;
  dewPoint: number | null;
  cloudCover: number | null;
  windSpeed: number | null;
  windDirection: number | null;
  precipChance: number | null;
  uvIndex: number | null;
};

type ForecastBaseline = {
  source: "twc";
  localDate: string;
  issuedAt: string | null;
  capturedAt: string;
  forecastHigh: number | null;
  points: ForecastPoint[];
};

type ForecastSnapshot = ForecastBaseline & {
  allPoints: ForecastPoint[];
  dailyHighs: Record<string, number | null>;
};

type BaselineDbRow = {
  local_date: string;
  issued_at: Date | null;
  captured_at: Date;
  forecast_high: number | null;
  points: ForecastPoint[];
};

type SnapshotDbRow = {
  captured_at: Date;
  issued_at: Date | null;
  points: ForecastPoint[];
  daily_highs: Record<string, number | null> | null;
};

declare global {
  var weatherBaselineMemoryV3: Map<string, ForecastBaseline> | undefined;
  var twcForecastMemoryV2: Map<string, { value: ForecastSnapshot; expires: number }> | undefined;
  var weatherBaselineTableReadyV3: Promise<void> | undefined;
}

const baselineMemory = global.weatherBaselineMemoryV3 ?? new Map<string, ForecastBaseline>();
const forecastMemory = global.twcForecastMemoryV2 ?? new Map<string, { value: ForecastSnapshot; expires: number }>();
if (!global.weatherBaselineMemoryV3) global.weatherBaselineMemoryV3 = baselineMemory;
if (!global.twcForecastMemoryV2) global.twcForecastMemoryV2 = forecastMemory;

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

function cToF(value: number | null) {
  return value === null ? null : value * 9 / 5 + 32;
}

function floorCToF(value: number | null) {
  const fahrenheit = cToF(value);
  return fahrenheit === null ? null : Math.floor(fahrenheit);
}

function floorF(value: number | null) {
  return value === null ? null : Math.floor(value);
}

function knotsToMph(value: number | null) {
  return value === null ? null : value * 1.150779448;
}

function parseMetarThermo(raw: string | null) {
  if (!raw) return null;
  const match = raw.match(/(?:^|\s)T([01])(\d{3})([01])(\d{3})(?=\s|$)/);
  if (!match) return null;
  const tempMagnitude = Number(match[2]) / 10;
  const dewMagnitude = Number(match[4]) / 10;
  return {
    tempC: match[1] === "1" ? -tempMagnitude : tempMagnitude,
    dewC: match[3] === "1" ? -dewMagnitude : dewMagnitude,
  };
}

function metarTemperatureFromF(raw: string | null, fallbackF: number | null) {
  const precise = parseMetarThermo(raw);
  return precise === null ? floorF(fallbackF) : floorCToF(precise.tempC);
}

function metarTemperatureFromC(raw: string | null, fallbackC: number | null) {
  const precise = parseMetarThermo(raw);
  return floorCToF(precise?.tempC ?? fallbackC);
}

function metarDewPointFromF(raw: string | null, fallbackF: number | null) {
  const precise = parseMetarThermo(raw);
  return precise === null ? fallbackF : cToF(precise.dewC);
}

function metarDewPointFromC(raw: string | null, fallbackC: number | null) {
  const precise = parseMetarThermo(raw);
  return cToF(precise?.dewC ?? fallbackC);
}

function cloudCoverFromRaw(raw: string | null) {
  if (!raw) return null;
  if (/(?:^|\s)(CLR|SKC|NSC)(?:\s|$)/.test(raw)) return 0;
  let cover: number | null = null;
  const weights: Record<string, number> = { FEW: 0.25, SCT: 0.5, BKN: 0.75, OVC: 1 };
  for (const match of raw.matchAll(/(?:^|\s)(FEW|SCT|BKN|OVC)\d{3}(?:[A-Z]{2,3})?(?=\s|$)/g)) {
    cover = Math.max(cover ?? 0, weights[match[1]] ?? 0);
  }
  return cover;
}

function hPaToInHg(value: number | null) {
  return value === null ? null : value * 0.0295299830714;
}

function rhFromC(tempC: number | null, dewC: number | null) {
  if (tempC === null || dewC === null) return null;
  const a = 17.625;
  const b = 243.04;
  const saturation = Math.exp((a * tempC) / (b + tempC));
  const actual = Math.exp((a * dewC) / (b + dewC));
  return Math.max(0, Math.min(100, 100 * actual / saturation));
}

function parseSixHourFromRaw(raw: string | null, group: "1" | "2") {
  if (!raw) return null;
  const match = raw.match(new RegExp(`(?:^|\\s)${group}([01])(\\d{3})(?=\\s|$)`));
  if (!match) return null;
  const c = Number(match[2]) / 10 * (match[1] === "1" ? -1 : 1);
  return floorCToF(c);
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
    dewPoint: keyFor(obs, "dew_point_temperature_set_"),
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
    const kind = classify(raw);
    const sourceTempF = num(valueAt(obs, keys.temp, index));
    const sourceDewF = num(valueAt(obs, keys.dewPoint, index));
    const sourceHigh6F = num(valueAt(obs, keys.high6, index));
    const sourceLow6F = num(valueAt(obs, keys.low6, index));
    return {
      time: str(date) ?? "",
      temp: kind === "official" ? metarTemperatureFromF(raw, sourceTempF) : sourceTempF,
      dewPoint: kind === "official" ? metarDewPointFromF(raw, sourceDewF) : sourceDewF,
      cloudCover: cloudCoverFromRaw(raw),
      rh: num(valueAt(obs, keys.rh, index)),
      windSpeed: num(valueAt(obs, keys.windSpeed, index)),
      windDirection: num(valueAt(obs, keys.windDirection, index)),
      altimeter: num(valueAt(obs, keys.altimeter, index)),
      seaLevelPressure: num(valueAt(obs, keys.seaLevelPressure, index)),
      high6: parseSixHourFromRaw(raw, "1") ?? floorF(sourceHigh6F),
      low6: parseSixHourFromRaw(raw, "2") ?? floorF(sourceLow6F),
      high24: floorF(num(valueAt(obs, keys.high24, index))),
      low24: floorF(num(valueAt(obs, keys.low24, index))),
      raw,
      kind,
      source: "synoptic",
      receivedAt: null,
    };
  });

  const latest = [...rows].reverse().find((row) => row.temp !== null || row.raw) ?? null;
  const hf = rows.filter((row) => row.kind === "hf").slice(-24).reverse();
  const official = rows.filter((row) => row.kind === "official").slice(-18).reverse();
  const sixHour = rows.filter((row) => row.high6 !== null || row.low6 !== null).slice(-6).reverse();
  const daily = rows.filter((row) => row.high24 !== null || row.low24 !== null).slice(-4).reverse();
  return { stid: station.STID, name: station.NAME, timezone: station.TIMEZONE, latest, hf, official, sixHour, daily, hfAvailable: hf.length > 0 };
}

function normalizeAwc(item: AnyRecord): { stid: string; row: Row } | null {
  const stid = str(item.icaoId);
  if (!stid) return null;
  const raw = str(item.rawOb);
  const tempC = num(item.temp);
  const dewC = num(item.dewp);
  const obsTime = num(item.obsTime);
  const time = obsTime !== null ? new Date(obsTime * 1000).toISOString() : str(item.reportTime) ?? str(item.receiptTime) ?? "";
  if (!time) return null;
  const maxFromRaw = parseSixHourFromRaw(raw, "1");
  const minFromRaw = parseSixHourFromRaw(raw, "2");
  const awcMax = floorCToF(num(item.maxT));
  const awcMin = floorCToF(num(item.minT));
  return {
    stid,
    row: {
      time,
      temp: metarTemperatureFromC(raw, tempC),
      dewPoint: metarDewPointFromC(raw, dewC),
      cloudCover: cloudCoverFromRaw(raw),
      rh: rhFromC(tempC, dewC),
      windSpeed: knotsToMph(num(item.wspd)),
      windDirection: num(item.wdir),
      altimeter: hPaToInHg(num(item.altim)),
      seaLevelPressure: num(item.slp),
      high6: maxFromRaw ?? awcMax,
      low6: minFromRaw ?? awcMin,
      high24: null,
      low24: null,
      raw,
      kind: "official",
      source: "awc",
      receivedAt: str(item.receiptTime),
    },
  };
}

async function fetchAwcLatest() {
  const url = new URL("https://aviationweather.gov/api/data/metar");
  url.searchParams.set("ids", STATIONS.map((station) => station.stid).join(","));
  url.searchParams.set("format", "json");
  const response = await fetch(url, { headers: AWC_HEADERS, cache: "no-store" });
  if (response.status === 204) return new Map<string, Row>();
  if (!response.ok) throw new Error(`AWC METAR request failed (${response.status})`);
  const payload = await response.json();
  const byStation = new Map<string, Row>();
  if (Array.isArray(payload)) {
    for (const item of payload) {
      const normalized = normalizeAwc(item as AnyRecord);
      if (!normalized) continue;
      const existing = byStation.get(normalized.stid);
      if (!existing || new Date(normalized.row.time).getTime() > new Date(existing.time).getTime()) byStation.set(normalized.stid, normalized.row);
    }
  }
  return byStation;
}

function mergeAwc<T extends { latest: Row | null; official: Row[]; sixHour: Row[] }>(station: T, awc: Row | undefined): T {
  if (!awc) return station;
  const sameReport = (row: Row) => row.raw === awc.raw || Math.abs(new Date(row.time).getTime() - new Date(awc.time).getTime()) < 30_000;
  const official = [awc, ...station.official.filter((row) => !sameReport(row))].sort((a, b) => new Date(b.time).getTime() - new Date(a.time).getTime()).slice(0, 18);
  const sixHour = awc.high6 !== null || awc.low6 !== null
    ? [awc, ...station.sixHour.filter((row) => !sameReport(row))].sort((a, b) => new Date(b.time).getTime() - new Date(a.time).getTime()).slice(0, 6)
    : station.sixHour;
  const latest = !station.latest || new Date(awc.time).getTime() >= new Date(station.latest.time).getTime() ? awc : station.latest;
  return { ...station, official, sixHour, latest };
}

function localDate(timezone: string, date = new Date()) {
  return new Intl.DateTimeFormat("en-CA", { timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
}

function twcKey() {
  return process.env.TWC_API_KEY || process.env.WEATHER_API_KEY || null;
}

async function ensureForecastTables() {
  if (!hasDatabase) return;
  if (!global.weatherBaselineTableReadyV3) {
    global.weatherBaselineTableReadyV3 = (async () => {
      await query(`
        CREATE TABLE IF NOT EXISTS weather_trajectory_baselines_v2 (
          stid text NOT NULL,
          local_date date NOT NULL,
          source text NOT NULL,
          timezone text NOT NULL,
          issued_at timestamptz,
          captured_at timestamptz NOT NULL DEFAULT now(),
          points jsonb NOT NULL,
          PRIMARY KEY (stid, local_date, source)
        )
      `);
      await query(`ALTER TABLE weather_trajectory_baselines_v2 ADD COLUMN IF NOT EXISTS forecast_high real`);
      await query(`
        CREATE TABLE IF NOT EXISTS weather_forecast_snapshots (
          stid text NOT NULL,
          source text NOT NULL,
          captured_at timestamptz NOT NULL,
          issued_at timestamptz,
          points jsonb NOT NULL,
          PRIMARY KEY (stid, source, captured_at)
        )
      `);
      await query(`ALTER TABLE weather_forecast_snapshots ADD COLUMN IF NOT EXISTS daily_highs jsonb`);
    })();
  }
  await global.weatherBaselineTableReadyV3;
}

function snapshotBucket(date = new Date()) {
  const bucket = 15 * 60 * 1000;
  return new Date(Math.floor(date.getTime() / bucket) * bucket).toISOString();
}

async function twcJson(path: string, config: (typeof STATIONS)[number], apiKey: string) {
  const url = new URL(`https://api.weather.com${path}`);
  url.searchParams.set("geocode", `${config.lat},${config.lon}`);
  url.searchParams.set("units", "e");
  url.searchParams.set("language", "en-US");
  url.searchParams.set("format", "json");
  url.searchParams.set("apiKey", apiKey);
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`TWC request failed for ${config.stid}: ${path} (${response.status})`);
  return response.json();
}

async function fetchTwcForecast(config: (typeof STATIONS)[number]): Promise<ForecastSnapshot> {
  const cached = forecastMemory.get(config.stid);
  if (cached && cached.expires > Date.now()) return cached.value;
  const apiKey = twcKey();
  if (!apiKey) throw new Error("TWC_API_KEY is not configured");

  const [hourlyRaw, dailyRaw] = await Promise.all([
    twcJson("/v3/wx/forecast/hourly/2day", config, apiKey),
    twcJson("/v3/wx/forecast/daily/3day", config, apiKey),
  ]);
  const hourly = (hourlyRaw?.["v3-wx-forecast-hourly-2day"] ?? hourlyRaw) as AnyRecord;
  const daily = (dailyRaw?.["v3-wx-forecast-daily-3day"] ?? dailyRaw) as AnyRecord;

  const times = Array.isArray(hourly.validTimeLocal) ? hourly.validTimeLocal : [];
  const temps = Array.isArray(hourly.temperature) ? hourly.temperature : [];
  const dew = Array.isArray(hourly.temperatureDewPoint) ? hourly.temperatureDewPoint : [];
  const clouds = Array.isArray(hourly.cloudCover) ? hourly.cloudCover : [];
  const windSpeed = Array.isArray(hourly.windSpeed) ? hourly.windSpeed : [];
  const windDirection = Array.isArray(hourly.windDirection) ? hourly.windDirection : [];
  const precipChance = Array.isArray(hourly.precipChance) ? hourly.precipChance : [];
  const uvIndex = Array.isArray(hourly.uvIndex) ? hourly.uvIndex : [];

  const allPoints: ForecastPoint[] = times.map((time, index) => ({
    time: String(time),
    temp: Number(temps[index]),
    dewPoint: num(dew[index]),
    cloudCover: num(clouds[index]) === null ? null : (num(clouds[index]) as number) / 100,
    windSpeed: num(windSpeed[index]),
    windDirection: num(windDirection[index]),
    precipChance: num(precipChance[index]),
    uvIndex: num(uvIndex[index]),
  })).filter((point) => Number.isFinite(point.temp));

  const dailyTimes = Array.isArray(daily.validTimeLocal) ? daily.validTimeLocal : [];
  const calendarMax = Array.isArray(daily.calendarDayTemperatureMax) ? daily.calendarDayTemperatureMax : [];
  const daypartMax = Array.isArray(daily.temperatureMax) ? daily.temperatureMax : [];
  const dailyHighs: Record<string, number | null> = {};
  dailyTimes.forEach((time, index) => {
    const date = String(time).slice(0, 10);
    dailyHighs[date] = num(calendarMax[index]) ?? num(daypartMax[index]);
  });

  const date = localDate(config.timezone);
  const points = allPoints.filter((point) => localDate(config.timezone, new Date(point.time)) === date);
  const capturedAt = new Date().toISOString();
  const value: ForecastSnapshot = {
    source: "twc",
    localDate: date,
    issuedAt: null,
    capturedAt,
    forecastHigh: dailyHighs[date] ?? null,
    points,
    allPoints,
    dailyHighs,
  };
  forecastMemory.set(config.stid, { value, expires: Date.now() + 10 * 60 * 1000 });

  if (hasDatabase) {
    await ensureForecastTables();
    await query(
      `INSERT INTO weather_forecast_snapshots (stid, source, captured_at, issued_at, points, daily_highs)
       VALUES ($1, 'twc', $2::timestamptz, NULL, $3::jsonb, $4::jsonb)
       ON CONFLICT (stid, source, captured_at) DO NOTHING`,
      [config.stid, snapshotBucket(new Date(capturedAt)), JSON.stringify(allPoints), JSON.stringify(dailyHighs)],
    );
  }
  return value;
}

async function getPreDaySnapshot(config: (typeof STATIONS)[number], date: string): Promise<ForecastBaseline | null> {
  if (!hasDatabase) return null;
  const result = await query<SnapshotDbRow>(
    `SELECT captured_at, issued_at, points, daily_highs
     FROM weather_forecast_snapshots
     WHERE stid = $1 AND source = 'twc'
       AND captured_at < ($2::date::timestamp AT TIME ZONE $3)
     ORDER BY captured_at DESC
     LIMIT 1`,
    [config.stid, date, config.timezone],
  );
  const row = result.rows[0];
  if (!row) return null;
  const points = (row.points ?? []).filter((point) => localDate(config.timezone, new Date(point.time)) === date);
  if (!points.length) return null;
  return {
    source: "twc",
    localDate: date,
    issuedAt: row.issued_at ? row.issued_at.toISOString() : null,
    capturedAt: row.captured_at.toISOString(),
    forecastHigh: row.daily_highs?.[date] ?? null,
    points,
  };
}

async function getDailyBaseline(config: (typeof STATIONS)[number], current: ForecastSnapshot | null): Promise<ForecastBaseline | null> {
  const date = localDate(config.timezone);
  const cacheKey = `${config.stid}:${date}:twc:v3`;
  const inMemory = baselineMemory.get(cacheKey);
  if (inMemory) return inMemory;

  try {
    await ensureForecastTables();
    if (hasDatabase) {
      const existing = await query<BaselineDbRow>(
        `SELECT local_date::text, issued_at, captured_at, forecast_high, points
         FROM weather_trajectory_baselines_v2
         WHERE stid = $1 AND local_date = $2::date AND source = 'twc'`,
        [config.stid, date],
      );
      if (existing.rows[0]) {
        const row = existing.rows[0];
        const baseline: ForecastBaseline = {
          source: "twc",
          localDate: row.local_date,
          issuedAt: row.issued_at ? row.issued_at.toISOString() : null,
          capturedAt: row.captured_at.toISOString(),
          forecastHigh: row.forecast_high,
          points: row.points,
        };
        baselineMemory.set(cacheKey, baseline);
        return baseline;
      }
    }

    const preDay = await getPreDaySnapshot(config, date);
    const fresh: ForecastBaseline | null = preDay ?? (current?.points.length ? {
      source: "twc",
      localDate: current.localDate,
      issuedAt: current.issuedAt,
      capturedAt: current.capturedAt,
      forecastHigh: current.forecastHigh,
      points: current.points,
    } : null);
    if (!fresh) return null;

    if (hasDatabase) {
      await query(
        `INSERT INTO weather_trajectory_baselines_v2 (stid, local_date, source, timezone, issued_at, captured_at, forecast_high, points)
         VALUES ($1, $2::date, 'twc', $3, $4::timestamptz, $5::timestamptz, $6, $7::jsonb)
         ON CONFLICT (stid, local_date, source) DO NOTHING`,
        [config.stid, fresh.localDate, config.timezone, fresh.issuedAt, fresh.capturedAt, fresh.forecastHigh, JSON.stringify(fresh.points)],
      );
    }
    baselineMemory.set(cacheKey, fresh);
    return fresh;
  } catch (error) {
    console.error(`Unable to build TWC baseline for ${config.stid}`, error);
    return null;
  }
}

export async function GET() {
  const token = process.env.SYNOPTIC_TOKEN;
  if (!token) return NextResponse.json({ error: "SYNOPTIC_TOKEN is not configured" }, { status: 500 });

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
    const [response, awcByStation, currentForecasts] = await Promise.all([
      fetch(url, { cache: "no-store" }),
      fetchAwcLatest().catch((error) => {
        console.error("AWC low-latency METAR fetch failed; falling back to Synoptic", error);
        return new Map<string, Row>();
      }),
      Promise.all(STATIONS.map((config) => fetchTwcForecast(config).catch((error) => {
        console.error(`TWC forecast unavailable for ${config.stid}`, error);
        return null;
      }))),
    ]);
    const baselines = await Promise.all(STATIONS.map((config, index) => getDailyBaseline(config, currentForecasts[index])));
    const payload = await response.json();

    if (!response.ok || payload?.SUMMARY?.RESPONSE_CODE !== 1) {
      return NextResponse.json({ error: payload?.SUMMARY?.RESPONSE_MESSAGE ?? "Synoptic request failed" }, { status: 502 });
    }

    const byId = new Map<string, AnyRecord>((payload.STATION ?? []).map((station: AnyRecord) => [String(station.STID), station]));
    const stations = STATIONS.map((config, index) => {
      const raw = byId.get(config.stid);
      const normalized = raw
        ? { ...config, ...normalizeStation(raw) }
        : { ...config, latest: null, hf: [], official: [], sixHour: [], daily: [], hfAvailable: false };
      const lowLatency = mergeAwc(normalized, awcByStation.get(config.stid));
      return { ...lowLatency, forecastBaseline: baselines[index], forecastCurrent: currentForecasts[index] };
    });

    return NextResponse.json(
      {
        updatedAt: new Date().toISOString(),
        stations,
        officialSource: "AWC-first / Synoptic-backup",
        forecastSource: "The Weather Company",
        forecastConfigured: Boolean(twcKey()),
        trajectoryModel: "twc-kalman-0.2-provisional",
      },
      { headers: { "Cache-Control": "no-store, max-age=0, must-revalidate" } },
    );
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : "Unable to fetch weather data" }, { status: 502 });
  }
}
