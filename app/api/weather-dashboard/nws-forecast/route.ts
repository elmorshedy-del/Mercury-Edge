import { NextResponse } from "next/server";
import { hasDatabase, query } from "@/lib/db";

export const dynamic = "force-dynamic";

const STATIONS = [
  { stid: "KNYC", city: "NYC", lat: 40.7789, lon: -73.9692, timezone: "America/New_York" },
  { stid: "KPHL", city: "PHL", lat: 39.8721, lon: -75.2411, timezone: "America/New_York" },
  { stid: "KLAX", city: "LA", lat: 33.9425, lon: -118.4081, timezone: "America/Los_Angeles" },
  { stid: "KDEN", city: "Denver", lat: 39.8561, lon: -104.6737, timezone: "America/Denver" },
  { stid: "KSEA", city: "Seattle", lat: 47.4447, lon: -122.3136, timezone: "America/Los_Angeles" },
];

const USER_AGENT = process.env.SOURCE_USER_AGENT || "Mercury-Edge weather trajectory research";
const NWS_HEADERS = { "User-Agent": USER_AGENT, Accept: "application/geo+json" };

type AnyRecord = Record<string, unknown>;
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
  source: "nws";
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
type GridValue = { validTime?: unknown; value?: unknown };
type GridField = { uom?: unknown; values?: unknown };
type Segment = { start: number; end: number; value: number | null };
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
  var nwsForecastMemoryV1: Map<string, { value: ForecastSnapshot; expires: number }> | undefined;
  var nwsBaselineMemoryV1: Map<string, ForecastBaseline> | undefined;
  var weatherBaselineTableReadyNwsV1: Promise<void> | undefined;
}

const forecastMemory = global.nwsForecastMemoryV1 ?? new Map<string, { value: ForecastSnapshot; expires: number }>();
const baselineMemory = global.nwsBaselineMemoryV1 ?? new Map<string, ForecastBaseline>();
if (!global.nwsForecastMemoryV1) global.nwsForecastMemoryV1 = forecastMemory;
if (!global.nwsBaselineMemoryV1) global.nwsBaselineMemoryV1 = baselineMemory;

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

function cToF(value: number | null) {
  return value === null ? null : value * 9 / 5 + 32;
}

function windToMph(value: number | null, uom: string) {
  if (value === null) return null;
  if (/km[_/]?h|km_h/i.test(uom)) return value * 0.6213711922;
  if (/m[_/]?s|m_s/i.test(uom)) return value * 2.2369362921;
  if (/kt|knot/i.test(uom)) return value * 1.150779448;
  return value;
}

function temperatureToF(value: number | null, uom: string) {
  if (value === null) return null;
  if (/degC/i.test(uom)) return cToF(value);
  if (/K$/i.test(uom) || /kelvin/i.test(uom)) return (value - 273.15) * 9 / 5 + 32;
  return value;
}

function durationMs(text: string) {
  const match = text.match(/^P(?:(\d+(?:\.\d+)?)D)?(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?$/);
  if (!match) return 60 * 60 * 1000;
  const days = Number(match[1] ?? 0);
  const hours = Number(match[2] ?? 0);
  const minutes = Number(match[3] ?? 0);
  const seconds = Number(match[4] ?? 0);
  return (((days * 24 + hours) * 60 + minutes) * 60 + seconds) * 1000;
}

function fieldSegments(field: GridField | null | undefined): Segment[] {
  if (!field || !Array.isArray(field.values)) return [];
  return (field.values as GridValue[]).map((item) => {
    const validTime = str(item.validTime);
    if (!validTime) return null;
    const [startText, durationText = "PT1H"] = validTime.split("/");
    const start = Date.parse(startText);
    if (!Number.isFinite(start)) return null;
    return { start, end: start + durationMs(durationText), value: num(item.value) };
  }).filter((item): item is Segment => item !== null).sort((a, b) => a.start - b.start);
}

function segmentValue(segments: Segment[], time: number) {
  const direct = segments.find((item) => time >= item.start && time < item.end);
  if (direct) return direct.value;
  const nearest = [...segments].sort((a, b) => Math.abs(a.start - time) - Math.abs(b.start - time))[0];
  return nearest && Math.abs(nearest.start - time) <= 90 * 60 * 1000 ? nearest.value : null;
}

function fieldUom(field: GridField | null | undefined) {
  return str(field?.uom) ?? "";
}

function snapshotBucket(date = new Date()) {
  const bucket = 15 * 60 * 1000;
  return new Date(Math.floor(date.getTime() / bucket) * bucket).toISOString();
}

async function ensureTables() {
  if (!hasDatabase) return;
  if (!global.weatherBaselineTableReadyNwsV1) {
    global.weatherBaselineTableReadyNwsV1 = (async () => {
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
  await global.weatherBaselineTableReadyNwsV1;
}

async function nwsJson(url: string) {
  const response = await fetch(url, { headers: NWS_HEADERS, cache: "no-store" });
  if (!response.ok) throw new Error(`NWS request failed (${response.status})`);
  return response.json() as Promise<AnyRecord>;
}

async function fetchNwsForecast(config: (typeof STATIONS)[number]): Promise<ForecastSnapshot> {
  const cached = forecastMemory.get(config.stid);
  if (cached && cached.expires > Date.now()) return cached.value;

  const point = await nwsJson(`https://api.weather.gov/points/${config.lat},${config.lon}`);
  const pointProps = (point.properties ?? {}) as AnyRecord;
  const gridUrl = str(pointProps.forecastGridData);
  if (!gridUrl) throw new Error(`NWS grid endpoint unavailable for ${config.stid}`);
  const grid = await nwsJson(gridUrl);
  const props = (grid.properties ?? {}) as AnyRecord;

  const temperature = props.temperature as GridField | undefined;
  const dewpoint = props.dewpoint as GridField | undefined;
  const skyCover = props.skyCover as GridField | undefined;
  const windSpeed = props.windSpeed as GridField | undefined;
  const windDirection = props.windDirection as GridField | undefined;
  const precipChance = props.probabilityOfPrecipitation as GridField | undefined;
  const maxTemperature = props.maxTemperature as GridField | undefined;

  const tempSeg = fieldSegments(temperature);
  if (!tempSeg.length) throw new Error(`NWS temperature grid is empty for ${config.stid}`);
  const dewSeg = fieldSegments(dewpoint);
  const cloudSeg = fieldSegments(skyCover);
  const windSpeedSeg = fieldSegments(windSpeed);
  const windDirectionSeg = fieldSegments(windDirection);
  const precipSeg = fieldSegments(precipChance);
  const maxSeg = fieldSegments(maxTemperature);

  const tempUom = fieldUom(temperature);
  const dewUom = fieldUom(dewpoint);
  const windUom = fieldUom(windSpeed);
  const first = Math.floor(tempSeg[0].start / 3_600_000) * 3_600_000;
  const finalTemp = tempSeg[tempSeg.length - 1];
  const last = Math.min(finalTemp.end, first + 72 * 3_600_000);
  const allPoints: ForecastPoint[] = [];

  for (let time = first; time < last; time += 3_600_000) {
    const temp = temperatureToF(segmentValue(tempSeg, time), tempUom);
    if (temp === null) continue;
    const cloud = segmentValue(cloudSeg, time);
    allPoints.push({
      time: new Date(time).toISOString(),
      temp,
      dewPoint: temperatureToF(segmentValue(dewSeg, time), dewUom),
      cloudCover: cloud === null ? null : Math.max(0, Math.min(1, cloud / 100)),
      windSpeed: windToMph(segmentValue(windSpeedSeg, time), windUom),
      windDirection: segmentValue(windDirectionSeg, time),
      precipChance: segmentValue(precipSeg, time),
      uvIndex: null,
    });
  }

  const dailyHighs: Record<string, number | null> = {};
  for (const segment of maxSeg) {
    const date = localDate(config.timezone, new Date(segment.start));
    dailyHighs[date] = temperatureToF(segment.value, fieldUom(maxTemperature));
  }
  for (const pointItem of allPoints) {
    const date = localDate(config.timezone, new Date(pointItem.time));
    if (dailyHighs[date] === undefined || dailyHighs[date] === null) {
      dailyHighs[date] = pointItem.temp;
    } else if (!maxSeg.length) {
      dailyHighs[date] = Math.max(dailyHighs[date] as number, pointItem.temp);
    }
  }

  const date = localDate(config.timezone);
  const capturedAt = new Date().toISOString();
  const issuedAt = str(props.updateTime) ?? str(props.validTimes)?.split("/")[0] ?? null;
  const value: ForecastSnapshot = {
    source: "nws",
    localDate: date,
    issuedAt,
    capturedAt,
    forecastHigh: dailyHighs[date] ?? null,
    points: allPoints.filter((pointItem) => localDate(config.timezone, new Date(pointItem.time)) === date),
    allPoints,
    dailyHighs,
  };
  forecastMemory.set(config.stid, { value, expires: Date.now() + 10 * 60 * 1000 });

  if (hasDatabase) {
    await ensureTables();
    await query(
      `INSERT INTO weather_forecast_snapshots (stid, source, captured_at, issued_at, points, daily_highs)
       VALUES ($1, 'nws', $2::timestamptz, $3::timestamptz, $4::jsonb, $5::jsonb)
       ON CONFLICT (stid, source, captured_at) DO NOTHING`,
      [config.stid, snapshotBucket(new Date(capturedAt)), issuedAt, JSON.stringify(allPoints), JSON.stringify(dailyHighs)],
    );
  }
  return value;
}

async function getBaseline(config: (typeof STATIONS)[number], current: ForecastSnapshot): Promise<ForecastBaseline> {
  const date = localDate(config.timezone);
  const cacheKey = `${config.stid}:${date}:nws:v1`;
  const cached = baselineMemory.get(cacheKey);
  if (cached) return cached;

  if (hasDatabase) {
    await ensureTables();
    const existing = await query<BaselineDbRow>(
      `SELECT local_date::text, issued_at, captured_at, forecast_high, points
       FROM weather_trajectory_baselines_v2
       WHERE stid = $1 AND local_date = $2::date AND source = 'nws'`,
      [config.stid, date],
    );
    if (existing.rows[0]) {
      const row = existing.rows[0];
      const baseline: ForecastBaseline = {
        source: "nws",
        localDate: row.local_date,
        issuedAt: row.issued_at ? row.issued_at.toISOString() : null,
        capturedAt: row.captured_at.toISOString(),
        forecastHigh: row.forecast_high,
        points: row.points,
      };
      baselineMemory.set(cacheKey, baseline);
      return baseline;
    }

    const preDay = await query<SnapshotDbRow>(
      `SELECT captured_at, issued_at, points, daily_highs
       FROM weather_forecast_snapshots
       WHERE stid = $1 AND source = 'nws'
         AND captured_at < ($2::date::timestamp AT TIME ZONE $3)
       ORDER BY captured_at DESC
       LIMIT 1`,
      [config.stid, date, config.timezone],
    );
    const row = preDay.rows[0];
    const preDayPoints = row?.points?.filter((point) => localDate(config.timezone, new Date(point.time)) === date) ?? [];
    const baseline: ForecastBaseline = preDayPoints.length ? {
      source: "nws",
      localDate: date,
      issuedAt: row.issued_at ? row.issued_at.toISOString() : null,
      capturedAt: row.captured_at.toISOString(),
      forecastHigh: row.daily_highs?.[date] ?? null,
      points: preDayPoints,
    } : {
      source: "nws",
      localDate: current.localDate,
      issuedAt: current.issuedAt,
      capturedAt: current.capturedAt,
      forecastHigh: current.forecastHigh,
      points: current.points,
    };

    await query(
      `INSERT INTO weather_trajectory_baselines_v2 (stid, local_date, source, timezone, issued_at, captured_at, forecast_high, points)
       VALUES ($1, $2::date, 'nws', $3, $4::timestamptz, $5::timestamptz, $6, $7::jsonb)
       ON CONFLICT (stid, local_date, source) DO NOTHING`,
      [config.stid, baseline.localDate, config.timezone, baseline.issuedAt, baseline.capturedAt, baseline.forecastHigh, JSON.stringify(baseline.points)],
    );
    baselineMemory.set(cacheKey, baseline);
    return baseline;
  }

  const baseline: ForecastBaseline = {
    source: "nws",
    localDate: current.localDate,
    issuedAt: current.issuedAt,
    capturedAt: current.capturedAt,
    forecastHigh: current.forecastHigh,
    points: current.points,
  };
  baselineMemory.set(cacheKey, baseline);
  return baseline;
}

export async function GET() {
  try {
    const current = await Promise.all(STATIONS.map((station) => fetchNwsForecast(station)));
    const baselines = await Promise.all(STATIONS.map((station, index) => getBaseline(station, current[index])));
    return NextResponse.json({
      updatedAt: new Date().toISOString(),
      source: "National Weather Service",
      forecasts: STATIONS.map((station, index) => ({
        stid: station.stid,
        baseline: baselines[index],
        current: current[index],
      })),
    }, { headers: { "Cache-Control": "no-store, max-age=0, must-revalidate" } });
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : "Unable to fetch NWS forecast" }, { status: 502 });
  }
}
