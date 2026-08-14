import { KALSHI_BASE_URL } from "../config";
import { fetchJson } from "../http";
import type { ContractBand, QuotePoint } from "../types";

type KalshiEventList = {
  cursor?: string;
  events: Array<{
    event_ticker: string;
    series_ticker: string;
    title: string;
    settlement_sources?: Array<{ name: string; url: string }>;
  }>;
};

type KalshiMarket = {
  ticker: string;
  event_ticker: string;
  yes_sub_title?: string;
  floor_strike?: number;
  cap_strike?: number;
  strike_type: "less" | "between" | "greater";
  result?: "yes" | "no" | "";
  expiration_value?: string;
  rules_primary?: string;
  [key: string]: unknown;
};

export type KalshiEventDetail = {
  event: {
    event_ticker: string;
    series_ticker: string;
    title: string;
    settlement_sources?: Array<{ name: string; url: string }>;
    markets?: KalshiMarket[];
    [key: string]: unknown;
  };
};

type Candle = {
  end_period_ts: number;
  price?: { close_dollars?: string; previous_dollars?: string };
  yes_bid?: { close_dollars?: string };
  yes_ask?: { close_dollars?: string };
  volume_fp?: string;
  open_interest_fp?: string;
};

function numberOrNull(value: unknown): number | null {
  if (value === undefined || value === null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function marketToBand(market: KalshiMarket): ContractBand {
  const label = market.yes_sub_title ?? market.ticker;
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
  } else {
    const floor = numberOrNull(market.floor_strike);
    lower = floor === null ? null : floor + 1;
  }

  return {
    ticker: market.ticker,
    label,
    lower,
    upper,
    result: market.result === "yes" || market.result === "no" ? market.result : "unknown",
  };
}

export async function listSettledEvents(seriesTicker: string, maxPages = 10) {
  const events: KalshiEventList["events"] = [];
  let cursor = "";
  for (let page = 0; page < maxPages; page += 1) {
    const params = new URLSearchParams({
      series_ticker: seriesTicker,
      status: "settled",
      limit: "200",
    });
    if (cursor) params.set("cursor", cursor);
    const payload = await fetchJson<KalshiEventList>(`${KALSHI_BASE_URL}/events?${params}`);
    events.push(...payload.events);
    cursor = payload.cursor ?? "";
    if (!cursor) break;
  }
  return events;
}

export async function getEvent(eventTicker: string) {
  return fetchJson<KalshiEventDetail>(
    `${KALSHI_BASE_URL}/events/${encodeURIComponent(eventTicker)}?with_nested_markets=true`,
  );
}

export async function getMinuteCandles(
  seriesTicker: string,
  marketTicker: string,
  start: Date,
  end: Date,
): Promise<QuotePoint[]> {
  const params = new URLSearchParams({
    start_ts: Math.floor(start.getTime() / 1000).toString(),
    end_ts: Math.floor(end.getTime() / 1000).toString(),
    period_interval: "1",
  });
  const payload = await fetchJson<{ candlesticks: Candle[] }>(
    `${KALSHI_BASE_URL}/series/${encodeURIComponent(seriesTicker)}/markets/${encodeURIComponent(marketTicker)}/candlesticks?${params}`,
  );
  return payload.candlesticks.map((candle) => ({
    contractTicker: marketTicker,
    capturedAt: new Date(candle.end_period_ts * 1000).toISOString(),
    yesBid: numberOrNull(candle.yes_bid?.close_dollars),
    yesAsk: numberOrNull(candle.yes_ask?.close_dollars),
    lastPrice: numberOrNull(candle.price?.close_dollars),
  }));
}
