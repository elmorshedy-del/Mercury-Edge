import { NextRequest, NextResponse } from "next/server";
import { hasDatabase, query } from "@/lib/db";
import { KALSHI_BASE_URL, STATIONS as CONFIG_STATIONS } from "@/lib/config";
import { getMinuteCandles } from "@/lib/sources/kalshi";
import { buildMarketCenter, quoteMid, type MarketSeries } from "@/lib/weather/market-reaction";

export const dynamic = "force-dynamic";

type AnyRecord = Record<string, unknown>;

type KalshiMarket = {
  ticker: string;
  event_ticker: string;
  title?: string;
  yes_sub_title?: string;
  strike_type?: "less" | "between" | "greater";
  floor_strike?: number | string | null;
  cap_strike?: number | string | null;
  close_time?: string;
  expected_expiration_time?: string;
  yes_bid_dollars?: string;
  yes_ask_dollars?: string;
  last_price_dollars?: string;
};

type SnapshotRow = {
  captured_at: Date;
  daily_highs: Record<string, number | null> | null;
};

const DASHBOARD_IDS: Record<string, string> = {
  KNYC: "KNYC",
  KPHL: "KPHL",
  KLAX: "KLAX",
  KDEN: "KDEN",
  KSEA: "KSEA",
};

function numberOrNull(value: unknown) {
  if (value === undefined || value === null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function localDate(iso: string | Date, timezone: string) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(typeof iso === "string" ? new Date(iso) : iso);
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

async function fetchMarkets(seriesTicker: string, status: "open" | "closed" | "settled") {
  const params = new URLSearchParams({ series_ticker: seriesTicker, status, limit: "200" });
  const response = await fetch(`${KALSHI_BASE_URL}/markets?${params}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`Kalshi markets request failed (${response.status})`);
  const payload = await response.json() as { markets?: KalshiMarket[] };
  return Array.isArray(payload.markets) ? payload.markets : [];
}

function chooseEvent(markets: KalshiMarket[], date: string, timezone: string) {
  const groups = new Map<string, KalshiMarket[]>();
  for (const market of markets) {
    const group = groups.get(market.event_ticker) ?? [];
    group.push(market);
    groups.set(market.event_ticker, group);
  }

  const candidates = [...groups.entries()].map(([eventTicker, items]) => {
    const close = items.map((item) => item.close_time ?? item.expected_expiration_time ?? "").find(Boolean) ?? "";
    const dateMatch = close ? localDate(close, timezone) === date : false;
    const closeMs = close ? new Date(close).getTime() : Number.POSITIVE_INFINITY;
    return { eventTicker, items, close, dateMatch, closeMs };
  });

  return candidates.find((candidate) => candidate.dateMatch)
    ?? candidates.sort((a, b) => a.closeMs - b.closeMs)[0]
    ?? null;
}

async function discoverEvent(seriesTicker: string, date: string, timezone: string) {
  for (const status of ["open", "closed", "settled"] as const) {
    const markets = await fetchMarkets(seriesTicker, status);
    const chosen = chooseEvent(markets, date, timezone);
    if (chosen?.items.length) return { ...chosen, status };
  }
  return null;
}

async function twcRevisionMarkers(stid: string, date: string) {
  if (!hasDatabase) return [];
  try {
    const result = await query<SnapshotRow>(
      `SELECT captured_at, daily_highs
       FROM weather_forecast_snapshots
       WHERE stid = $1 AND source = 'twc'
         AND captured_at >= now() - interval '36 hours'
       ORDER BY captured_at ASC`,
      [stid],
    );
    const markers: Array<{ time: string; forecastHigh: number; delta: number | null }> = [];
    let prior: number | null = null;
    for (const row of result.rows) {
      const high = row.daily_highs?.[date];
      if (typeof high !== "number" || !Number.isFinite(high)) continue;
      if (prior === null || high !== prior) {
        markers.push({
          time: row.captured_at.toISOString(),
          forecastHigh: high,
          delta: prior === null ? null : high - prior,
        });
        prior = high;
      }
    }
    return markers;
  } catch (error) {
    console.error("Unable to load TWC revision markers", error);
    return [];
  }
}

export async function GET(request: NextRequest) {
  const stid = request.nextUrl.searchParams.get("stid")?.toUpperCase() ?? "KNYC";
  const canonical = DASHBOARD_IDS[stid];
  const station = CONFIG_STATIONS.find((item) => item.station === canonical);
  if (!station) return NextResponse.json({ error: `Unsupported station ${stid}` }, { status: 400 });

  const targetDate = request.nextUrl.searchParams.get("date") ?? localDate(new Date(), station.timezone);

  try {
    const event = await discoverEvent(station.kalshiSeries, targetDate, station.timezone);
    if (!event) {
      return NextResponse.json({
        stid,
        date: targetDate,
        seriesTicker: station.kalshiSeries,
        eventTicker: null,
        markets: [],
        marketCenter: [],
        twcRevisions: await twcRevisionMarkers(stid, targetDate),
      });
    }

    const now = new Date();
    const start = new Date(now.getTime() - 36 * 60 * 60 * 1000);
    const end = new Date(now.getTime() + 5 * 60 * 1000);
    const marketSeries: MarketSeries[] = await Promise.all(event.items.map(async (market) => {
      const band = bandFromMarket(market);
      const quotes = await getMinuteCandles(station.kalshiSeries, market.ticker, start, end)
        .then((points) => points
          .filter((point) => localDate(point.capturedAt, station.timezone) === targetDate)
          .map((point) => ({
            time: point.capturedAt,
            yesBid: point.yesBid,
            yesAsk: point.yesAsk,
            lastPrice: point.lastPrice,
          })))
        .catch((error) => {
          console.error(`Unable to load candles for ${market.ticker}`, error);
          return [];
        });

      if (!quotes.length) {
        quotes.push({
          time: now.toISOString(),
          yesBid: numberOrNull(market.yes_bid_dollars),
          yesAsk: numberOrNull(market.yes_ask_dollars),
          lastPrice: numberOrNull(market.last_price_dollars),
        });
      }
      return { ...band, quotes };
    }));

    const marketCenter = buildMarketCenter(marketSeries);
    const latestMarkets = marketSeries.map((market) => {
      const latest = [...market.quotes].sort((a, b) => new Date(b.time).getTime() - new Date(a.time).getTime())[0] ?? null;
      return { ...market, latestMid: latest ? quoteMid(latest) : null };
    });
    const leader = [...latestMarkets]
      .filter((market) => market.latestMid !== null)
      .sort((a, b) => (b.latestMid as number) - (a.latestMid as number))[0] ?? null;

    return NextResponse.json({
      stid,
      date: targetDate,
      timezone: station.timezone,
      seriesTicker: station.kalshiSeries,
      eventTicker: event.eventTicker,
      eventStatus: event.status,
      markets: latestMarkets,
      marketCenter,
      leader: leader ? { ticker: leader.ticker, label: leader.label, probability: leader.latestMid } : null,
      twcRevisions: await twcRevisionMarkers(stid, targetDate),
      updatedAt: new Date().toISOString(),
    }, { headers: { "Cache-Control": "public, max-age=15, stale-while-revalidate=30" } });
  } catch (error) {
    return NextResponse.json({
      error: error instanceof Error ? error.message : "Unable to build market reaction chart",
    }, { status: 502 });
  }
}
