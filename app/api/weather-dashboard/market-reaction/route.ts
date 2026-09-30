import { NextRequest, NextResponse } from "next/server";
import { hasDatabase, query } from "@/lib/db";
import { KALSHI_BASE_URL, STATIONS as CONFIG_STATIONS } from "@/lib/config";
import { getMinuteCandles } from "@/lib/sources/kalshi";
import { buildMarketCenter, quoteMid, type MarketQuote, type MarketSeries } from "@/lib/weather/market-reaction";

export const dynamic = "force-dynamic";

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
  result?: string;
  settlement_value?: number | string | null;
};

type KalshiEventPayload = {
  event?: {
    event_ticker: string;
    series_ticker?: string;
    markets?: KalshiMarket[];
  };
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

const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

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

function exactEventTicker(seriesTicker: string, date: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  if (!match) return null;
  const monthIndex = Number(match[2]) - 1;
  const month = MONTHS[monthIndex];
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

async function fetchExactEvent(seriesTicker: string, date: string) {
  const eventTicker = exactEventTicker(seriesTicker, date);
  if (!eventTicker) throw new Error(`Invalid trading date ${date}`);

  const response = await fetch(
    `${KALSHI_BASE_URL}/events/${encodeURIComponent(eventTicker)}?with_nested_markets=true`,
    { cache: "no-store" },
  );
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`Kalshi event request failed (${response.status})`);

  const payload = await response.json() as KalshiEventPayload;
  const event = payload.event;
  if (!event || event.event_ticker !== eventTicker) {
    throw new Error(`Kalshi returned the wrong event for ${date}`);
  }
  const markets = Array.isArray(event.markets) ? event.markets : [];
  return { eventTicker, markets };
}

async function twcRevisionMarkers(stid: string, date: string) {
  if (!hasDatabase) return [];
  try {
    const result = await query<SnapshotRow>(
      `SELECT captured_at, daily_highs
       FROM weather_forecast_snapshots
       WHERE stid = $1 AND source = 'twc'
         AND captured_at >= ($2::date - interval '2 days')
         AND captured_at < ($2::date + interval '2 days')
       ORDER BY captured_at ASC`,
      [stid, date],
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
  const expectedEventTicker = exactEventTicker(station.kalshiSeries, targetDate);
  if (!expectedEventTicker) {
    return NextResponse.json({ error: `Invalid date ${targetDate}` }, { status: 400 });
  }

  try {
    const event = await fetchExactEvent(station.kalshiSeries, targetDate);
    if (!event) {
      return NextResponse.json({
        stid,
        date: targetDate,
        timezone: station.timezone,
        seriesTicker: station.kalshiSeries,
        expectedEventTicker,
        eventTicker: null,
        markets: [],
        marketCenter: [],
        leader: null,
        winningBucket: null,
        twcRevisions: await twcRevisionMarkers(stid, targetDate),
        updatedAt: new Date().toISOString(),
      }, { headers: { "Cache-Control": "no-store, max-age=0, must-revalidate" } });
    }

    const now = new Date();
    // Pull a wide UTC window around the requested local calendar day, then
    // filter every candle back to that station's local date. This works for
    // both live and archived days instead of silently limiting history to 36h.
    const targetNoonUtc = new Date(`${targetDate}T12:00:00.000Z`);
    const start = new Date(targetNoonUtc.getTime() - 30 * 60 * 60 * 1000);
    const end = new Date(targetNoonUtc.getTime() + 30 * 60 * 60 * 1000);
    const marketSeries: MarketSeries[] = await Promise.all(event.markets.map(async (market) => {
      const band = bandFromMarket(market);
      const quotes: MarketQuote[] = await getMinuteCandles(station.kalshiSeries, market.ticker, start, end)
        .then((points) => points
          .filter((point) => localDate(point.capturedAt, station.timezone) === targetDate)
          .map((point): MarketQuote => ({
            time: point.capturedAt,
            yesBid: point.yesBid,
            yesAsk: point.yesAsk,
            lastPrice: point.lastPrice ?? null,
          })))
        .catch((error): MarketQuote[] => {
          console.error(`Unable to load candles for ${market.ticker}`, error);
          return [];
        });

      if (!quotes.length) {
        const fallbackTime = market.close_time && localDate(market.close_time, station.timezone) === targetDate
          ? market.close_time
          : `${targetDate}T23:59:00.000Z`;
        quotes.push({
          time: fallbackTime,
          yesBid: numberOrNull(market.yes_bid_dollars),
          yesAsk: numberOrNull(market.yes_ask_dollars),
          lastPrice: numberOrNull(market.last_price_dollars),
        });
      }
      return { ...band, quotes };
    }));

    const winningMarket = event.markets.find((market) => String(market.result ?? "").toLowerCase() === "yes") ?? null;
    const winningBand = winningMarket ? bandFromMarket(winningMarket) : null;
    const settlementValue = winningMarket ? numberOrNull(winningMarket.settlement_value) : null;

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
      expectedEventTicker,
      eventTicker: event.eventTicker,
      markets: latestMarkets,
      marketCenter,
      leader: leader ? { ticker: leader.ticker, label: leader.label, probability: leader.latestMid } : null,
      winningBucket: winningBand ? { ...winningBand, settlementValue } : null,
      twcRevisions: await twcRevisionMarkers(stid, targetDate),
      updatedAt: new Date().toISOString(),
    }, { headers: { "Cache-Control": "no-store, max-age=0, must-revalidate" } });
  } catch (error) {
    return NextResponse.json({
      error: error instanceof Error ? error.message : "Unable to build market reaction chart",
    }, { status: 502 });
  }
}
