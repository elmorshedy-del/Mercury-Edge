import { NextResponse } from "next/server";
import { hasDatabase, query } from "@/lib/db";
import { KALSHI_BASE_URL, STATIONS } from "@/lib/config";

export const dynamic = "force-dynamic";

type KalshiMarket = {
  ticker: string;
  title?: string;
  yes_sub_title?: string;
  strike_type?: "less" | "between" | "greater";
  floor_strike?: number | string | null;
  cap_strike?: number | string | null;
  result?: string;
  expiration_value?: number | string | null;
  settlement_value?: number | string | null;
};

type KalshiEventPayload = {
  event?: {
    event_ticker: string;
    markets?: KalshiMarket[];
  };
};

type BaselineRow = {
  captured_at: Date;
  forecast_high: number | null;
};

type SnapshotRow = {
  captured_at: Date;
  forecast_high: number | null;
};

const STUDY_STATIONS = [
  { stid: "KNYC", label: "NYC" },
  { stid: "KPHL", label: "Philadelphia" },
  { stid: "KLAX", label: "LAX" },
  { stid: "KDEN", label: "Denver" },
  { stid: "KSEA", label: "Seattle" },
] as const;

const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

function numberOrNull(value: unknown) {
  if (value === undefined || value === null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function dateInZone(date: Date, timezone: string) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const year = parts.find((part) => part.type === "year")?.value;
  const month = parts.find((part) => part.type === "month")?.value;
  const day = parts.find((part) => part.type === "day")?.value;
  if (!year || !month || !day) throw new Error(`Unable to resolve local date for ${timezone}`);
  return `${year}-${month}-${day}`;
}

function addDays(date: string, delta: number) {
  const value = new Date(`${date}T12:00:00.000Z`);
  value.setUTCDate(value.getUTCDate() + delta);
  return value.toISOString().slice(0, 10);
}

async function completedDates() {
  // Pacific time is the latest civil day among the five dashboard cities.
  // Using its yesterday guarantees every displayed market day is complete.
  const pacificToday = dateInZone(new Date(), "America/Los_Angeles");
  const latest = addDays(pacificToday, -1);

  // The study is cumulative: start at the first day for which Mercury began
  // recording a frozen TWC trajectory baseline, then retain every completed
  // calendar day forever. Missing station/day cells remain explicit in the UI.
  const firstRecorded = await query<{ first_date: string | null }>(
    `SELECT MIN(local_date)::text AS first_date
     FROM weather_trajectory_baselines_v2
     WHERE source = 'twc'
       AND stid = ANY($1::text[])
       AND local_date <= $2::date`,
    [STUDY_STATIONS.map((station) => station.stid), latest],
  );

  const first = firstRecorded.rows[0]?.first_date ?? latest;
  const dates: string[] = [];
  for (let date = first; date <= latest; date = addDays(date, 1)) {
    dates.push(date);
  }
  return dates;
}

function exactEventTicker(seriesTicker: string, date: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  if (!match) return null;
  const month = MONTHS[Number(match[2]) - 1];
  if (!month) return null;
  return `${seriesTicker}-${match[1].slice(-2)}${month}${match[3]}`;
}

function bandFromMarket(market: KalshiMarket) {
  const label = market.yes_sub_title ?? market.title ?? market.ticker;
  const range = label.match(/(-?\d+)°?\s+to\s+(-?\d+)/i);
  const below = label.match(/(-?\d+)°?\s+or\s+below/i);
  const above = label.match(/(-?\d+)°?\s+or\s+above/i);
  let lower: number | null = null;
  let upper: number | null = null;

  if (range) {
    lower = Number(range[1]);
    upper = Number(range[2]);
  } else if (below) {
    upper = Number(below[1]);
  } else if (above) {
    lower = Number(above[1]);
  } else if (market.strike_type === "between") {
    lower = numberOrNull(market.floor_strike);
    upper = numberOrNull(market.cap_strike);
  } else if (market.strike_type === "less") {
    const cap = numberOrNull(market.cap_strike);
    upper = cap === null ? null : cap - 1;
  } else if (market.strike_type === "greater") {
    const floor = numberOrNull(market.floor_strike);
    lower = floor === null ? null : floor + 1;
  }

  return { ticker: market.ticker, label, lower, upper };
}

function inBand(value: number, lower: number | null, upper: number | null) {
  if (lower !== null && value < lower) return false;
  if (upper !== null && value > upper) return false;
  return true;
}

async function frozenTwcHigh(stid: string, date: string, timezone: string) {
  if (!hasDatabase) return null;

  // A study baseline must be information available before the target local day.
  // Do not accept a same-day fallback baseline as if it were pre-day truth.
  const baseline = await query<BaselineRow>(
    `SELECT captured_at, forecast_high
     FROM weather_trajectory_baselines_v2
     WHERE stid = $1
       AND local_date = $2::date
       AND source = 'twc'
       AND captured_at < ($2::date::timestamp AT TIME ZONE $3)
     ORDER BY captured_at DESC
     LIMIT 1`,
    [stid, date, timezone],
  );
  if (baseline.rows[0]?.forecast_high !== null && baseline.rows[0]?.forecast_high !== undefined) {
    return {
      forecastHigh: baseline.rows[0].forecast_high,
      capturedAt: baseline.rows[0].captured_at.toISOString(),
      provenance: "daily frozen baseline",
    };
  }

  const snapshot = await query<SnapshotRow>(
    `SELECT captured_at,
            NULLIF(daily_highs ->> ($2::text), '')::real AS forecast_high
     FROM weather_forecast_snapshots
     WHERE stid = $1
       AND source = 'twc'
       AND captured_at < ($2::date::timestamp AT TIME ZONE $3)
       AND daily_highs ? ($2::text)
     ORDER BY captured_at DESC
     LIMIT 1`,
    [stid, date, timezone],
  );
  const row = snapshot.rows[0];
  if (!row || row.forecast_high === null) return null;
  return {
    forecastHigh: row.forecast_high,
    capturedAt: row.captured_at.toISOString(),
    provenance: "latest pre-day snapshot",
  };
}

async function settledKalshiDay(seriesTicker: string, date: string) {
  const eventTicker = exactEventTicker(seriesTicker, date);
  if (!eventTicker) return null;

  const response = await fetch(
    `${KALSHI_BASE_URL}/events/${encodeURIComponent(eventTicker)}?with_nested_markets=true`,
    { next: { revalidate: 300 } },
  );
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`Kalshi event request failed for ${eventTicker} (${response.status})`);

  const payload = await response.json() as KalshiEventPayload;
  const event = payload.event;
  if (!event || event.event_ticker !== eventTicker) {
    throw new Error(`Kalshi returned the wrong event for ${eventTicker}`);
  }

  const markets = Array.isArray(event.markets) ? event.markets : [];
  const winner = markets.find((market) => String(market.result ?? "").toLowerCase() === "yes") ?? null;
  if (!winner) return { eventTicker, settled: false, realizedHigh: null, winningBucket: null };

  const band = bandFromMarket(winner);
  const realizedHigh =
    numberOrNull(winner.expiration_value) ??
    numberOrNull(markets.find((market) => market.expiration_value !== null && market.expiration_value !== undefined)?.expiration_value) ??
    numberOrNull(winner.settlement_value) ??
    numberOrNull(markets.find((market) => market.settlement_value !== null && market.settlement_value !== undefined)?.settlement_value);

  return {
    eventTicker,
    settled: true,
    realizedHigh,
    winningBucket: band,
  };
}

export async function GET() {
  if (!hasDatabase) {
    return NextResponse.json({ error: "Study requires the dashboard database." }, { status: 503 });
  }

  try {
    const dates = await completedDates();
    const rows = await Promise.all(STUDY_STATIONS.map(async ({ stid, label }) => {
      const config = STATIONS.find((station) => station.station === stid);
      if (!config) throw new Error(`Station config missing for ${stid}`);

      const values = await Promise.all(dates.map(async (date) => {
        const [baseline, outcome] = await Promise.all([
          frozenTwcHigh(stid, date, config.timezone),
          settledKalshiDay(config.kalshiSeries, date),
        ]);

        const forecastHigh = baseline?.forecastHigh ?? null;
        const realizedHigh = outcome?.realizedHigh ?? null;
        const error = forecastHigh !== null && realizedHigh !== null ? forecastHigh - realizedHigh : null;
        const hit = forecastHigh !== null && outcome?.settled && outcome.winningBucket
          ? inBand(forecastHigh, outcome.winningBucket.lower, outcome.winningBucket.upper)
          : null;

        return {
          date,
          forecastHigh,
          realizedHigh,
          error,
          hit,
          forecastCapturedAt: baseline?.capturedAt ?? null,
          forecastProvenance: baseline?.provenance ?? null,
          eventTicker: outcome?.eventTicker ?? null,
          winningBucket: outcome?.winningBucket ?? null,
          settled: outcome?.settled ?? false,
        };
      }));

      const absoluteErrors = values
        .map((item) => item.error)
        .filter((value): value is number => value !== null)
        .map((value) => Math.abs(value));
      const hitValues = values
        .map((item) => item.hit)
        .filter((value): value is boolean => value !== null);

      return {
        stid,
        city: label,
        values,
        mae: absoluteErrors.length
          ? absoluteErrors.reduce((sum, value) => sum + value, 0) / absoluteErrors.length
          : null,
        hits: hitValues.filter(Boolean).length,
        hitTotal: hitValues.length,
      };
    }));

    return NextResponse.json({
      dates,
      rows,
      updatedAt: new Date().toISOString(),
      definition: "Frozen TWC calendar-day high captured before local midnight → Kalshi/TWC settlement high.",
    }, { headers: { "Cache-Control": "no-store, max-age=0, must-revalidate" } });
  } catch (error) {
    console.error("Weather study build failed", error);
    return NextResponse.json({
      error: error instanceof Error ? error.message : "Unable to build weather study",
    }, { status: 502 });
  }
}
