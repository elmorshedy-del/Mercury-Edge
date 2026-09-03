import { KALSHI_BASE_URL } from "../config";
import { fetchJson } from "../http";
import type { ContractBand, MarketTrade, QuotePoint } from "../types";

type KalshiEventList = {
  cursor?: string;
  events: Array<{
    event_ticker: string;
    series_ticker: string;
    title: string;
    settlement_sources?: Array<{ name: string; url: string }>;
  }>;
};

export type KalshiMarket = {
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
  price?: CandleValues;
  yes_bid?: CandleValues;
  yes_ask?: CandleValues;
  volume_fp?: string;
  open_interest_fp?: string;
};

type CandleValues = Partial<Record<
  | "open" | "low" | "high" | "close" | "mean" | "previous" | "min" | "max"
  | "open_dollars" | "low_dollars" | "high_dollars" | "close_dollars"
  | "mean_dollars" | "previous_dollars" | "min_dollars" | "max_dollars",
  string
>>;

type KalshiTrade = {
  trade_id: string;
  ticker: string;
  count_fp: string;
  yes_price_dollars: string;
  no_price_dollars: string;
  taker_outcome_side?: "yes" | "no" | null;
  taker_book_side?: "bid" | "ask" | null;
  taker_side?: "yes" | "no" | null;
  created_time: string;
  is_block_trade?: boolean;
};

type HistoricalCutoff = {
  market_settled_ts: string;
  trades_created_ts: string;
};

let cutoffPromise: Promise<HistoricalCutoff> | null = null;
let cutoffFetchedAt = 0;

function numberOrNull(value: unknown): number | null {
  if (value === undefined || value === null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function candleNumber(values: CandleValues | undefined, field: "open" | "low" | "high" | "close") {
  return numberOrNull(values?.[`${field}_dollars`] ?? values?.[field]);
}

async function getHistoricalCutoff() {
  if (!cutoffPromise || Date.now() - cutoffFetchedAt > 5 * 60_000) {
    cutoffFetchedAt = Date.now();
    cutoffPromise = fetchJson<HistoricalCutoff>(`${KALSHI_BASE_URL}/historical/cutoff`)
      .catch((error) => {
        cutoffPromise = null;
        throw error;
      });
  }
  return cutoffPromise;
}

async function getHistoricalMarkets(eventTicker: string) {
  const markets: KalshiMarket[] = [];
  let cursor = "";
  do {
    const params = new URLSearchParams({ event_ticker: eventTicker, limit: "1000" });
    if (cursor) params.set("cursor", cursor);
    const payload = await fetchJson<{ markets: KalshiMarket[]; cursor?: string }>(
      `${KALSHI_BASE_URL}/historical/markets?${params}`,
    );
    markets.push(...payload.markets);
    cursor = payload.cursor ?? "";
  } while (cursor);
  return markets;
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
  const detail = await fetchJson<KalshiEventDetail>(
    `${KALSHI_BASE_URL}/events/${encodeURIComponent(eventTicker)}?with_nested_markets=true`,
  );
  if (detail.event.markets?.length) return detail;
  detail.event.markets = await getHistoricalMarkets(eventTicker);
  return detail;
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
  let payload: { candlesticks: Candle[] };
  try {
    payload = await fetchJson<{ candlesticks: Candle[] }>(
      `${KALSHI_BASE_URL}/series/${encodeURIComponent(seriesTicker)}/markets/${encodeURIComponent(marketTicker)}/candlesticks?${params}`,
    );
    // Older nested markets can disappear from the live partition without the
    // live candle endpoint consistently returning an error. Retry an empty
    // live result against the historical partition before accepting it.
    if (!payload.candlesticks.length) {
      try {
        const historical = await fetchJson<{ candlesticks: Candle[] }>(
          `${KALSHI_BASE_URL}/historical/markets/${encodeURIComponent(marketTicker)}/candlesticks?${params}`,
        );
        if (historical.candlesticks.length) payload = historical;
      } catch {
        // A genuinely empty current market is not an ingestion error.
      }
    }
  } catch (liveError) {
    try {
      payload = await fetchJson<{ candlesticks: Candle[] }>(
        `${KALSHI_BASE_URL}/historical/markets/${encodeURIComponent(marketTicker)}/candlesticks?${params}`,
      );
    } catch (historicalError) {
      throw new AggregateError([liveError, historicalError], `Unable to fetch candles for ${marketTicker}`);
    }
  }
  return payload.candlesticks.map((candle) => ({
    contractTicker: marketTicker,
    capturedAt: new Date(candle.end_period_ts * 1000).toISOString(),
    yesBid: candleNumber(candle.yes_bid, "close"),
    yesAsk: candleNumber(candle.yes_ask, "close"),
    yesBidOpen: candleNumber(candle.yes_bid, "open"),
    yesBidLow: candleNumber(candle.yes_bid, "low"),
    yesBidHigh: candleNumber(candle.yes_bid, "high"),
    yesAskOpen: candleNumber(candle.yes_ask, "open"),
    yesAskLow: candleNumber(candle.yes_ask, "low"),
    yesAskHigh: candleNumber(candle.yes_ask, "high"),
    lastPrice: candleNumber(candle.price, "close"),
    lastPriceOpen: candleNumber(candle.price, "open"),
    lastPriceLow: candleNumber(candle.price, "low"),
    lastPriceHigh: candleNumber(candle.price, "high"),
    sourcePrecision: "minute_candle",
  }));
}

async function fetchTradesFrom(
  path: "/markets/trades" | "/historical/trades",
  marketTicker: string,
  start: Date,
  end: Date,
) {
  const trades: KalshiTrade[] = [];
  let cursor = "";
  do {
    const params = new URLSearchParams({
      ticker: marketTicker,
      min_ts: Math.floor(start.getTime() / 1000).toString(),
      max_ts: Math.floor(end.getTime() / 1000).toString(),
      is_block_trade: "false",
      limit: "1000",
    });
    if (cursor) params.set("cursor", cursor);
    const payload = await fetchJson<{ trades: KalshiTrade[]; cursor?: string }>(
      `${KALSHI_BASE_URL}${path}?${params}`,
    );
    trades.push(...payload.trades);
    cursor = payload.cursor ?? "";
  } while (cursor);
  return trades;
}

export async function getMarketTrades(
  marketTicker: string,
  start: Date,
  end: Date,
): Promise<MarketTrade[]> {
  const cutoff = await getHistoricalCutoff();
  const cutoffMs = new Date(cutoff.trades_created_ts).getTime();
  const requests: Array<Promise<KalshiTrade[]>> = [];
  if (start.getTime() < cutoffMs) {
    requests.push(fetchTradesFrom(
      "/historical/trades",
      marketTicker,
      start,
      new Date(Math.min(end.getTime(), cutoffMs)),
    ));
  }
  if (end.getTime() >= cutoffMs) {
    requests.push(fetchTradesFrom(
      "/markets/trades",
      marketTicker,
      new Date(Math.max(start.getTime(), cutoffMs)),
      end,
    ));
  }
  const rows = (await Promise.all(requests)).flat();
  const unique = new Map(rows.map((trade) => [trade.trade_id, trade]));
  return [...unique.values()]
    .map((trade) => ({
      tradeId: trade.trade_id,
      contractTicker: trade.ticker,
      createdAt: new Date(trade.created_time).toISOString(),
      yesPrice: Number(trade.yes_price_dollars),
      noPrice: Number(trade.no_price_dollars),
      quantity: Number(trade.count_fp),
      takerOutcomeSide: trade.taker_outcome_side ?? trade.taker_side ?? null,
      takerBookSide: trade.taker_book_side ?? null,
      isBlockTrade: Boolean(trade.is_block_trade),
    }))
    .filter((trade) => Number.isFinite(trade.yesPrice) && Number.isFinite(trade.noPrice) && Number.isFinite(trade.quantity))
    .sort((a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime());
}
