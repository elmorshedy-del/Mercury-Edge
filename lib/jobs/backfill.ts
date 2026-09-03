import type { StationConfig } from "../config";
import { pool } from "../db";
import { getMetars } from "../sources/awc";
import { getHighFrequencyArchive } from "../sources/iem";
import { getEvent, getMarketTrades, getMinuteCandles, marketToBand } from "../sources/kalshi";
import { eventTickerForDate, isLocalDate } from "../time";
import type { ObservationPoint } from "../types";

export type BackfillDayResult = {
  station: string;
  date: string;
  eventTicker: string;
  contracts: number;
  quotes: number;
  trades: number;
  observations: number;
  actualReceiptObservations: number;
  discoveryOnlyObservations: number;
};

async function seedStation(station: StationConfig) {
  await pool!.query(
    `INSERT INTO stations
      (station_code, city, timezone, kalshi_series, nws_location, iem_network)
     VALUES ($1,$2,$3,$4,$5,$6)
     ON CONFLICT (station_code) DO UPDATE SET
      city=EXCLUDED.city, timezone=EXCLUDED.timezone,
      kalshi_series=EXCLUDED.kalshi_series,
      nws_location=EXCLUDED.nws_location, iem_network=EXCLUDED.iem_network`,
    [station.station, station.city, station.timezone, station.kalshiSeries,
     station.nwsLocation, station.iemNetwork],
  );
}

async function saveObservations(points: ObservationPoint[]) {
  for (const point of points) {
    await pool!.query(
      `INSERT INTO weather_observations
        (station_code, source, report_type, observed_at, received_at, receipt_quality,
         temperature_f, max_temperature_f, max_temperature_kind, settlement_compatible,
         source_precision, raw_text, raw_payload)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
       ON CONFLICT (station_code, source, report_type, observed_at, raw_text) DO UPDATE SET
        received_at=EXCLUDED.received_at,
        receipt_quality=EXCLUDED.receipt_quality,
        temperature_f=EXCLUDED.temperature_f,
        max_temperature_f=EXCLUDED.max_temperature_f,
        max_temperature_kind=EXCLUDED.max_temperature_kind,
        settlement_compatible=EXCLUDED.settlement_compatible`,
      [point.station, point.source, point.reportType, point.observedAt, point.receivedAt,
       point.receiptQuality, point.temperatureF, point.maxTemperatureF ?? null,
       point.maxTemperatureKind ?? null, point.settlementCompatible,
       point.source === "NOAA_AWC" ? "0.1C source / exact conversion" : "0.1C MADIS archive",
       point.rawText ?? "", point.payload ?? {}],
    );
  }
}

export async function backfillDay(
  station: StationConfig,
  date: string,
  discoveredAt = new Date(),
): Promise<BackfillDayResult> {
  if (!pool) throw new Error("DATABASE_URL is required for backfill");
  await seedStation(station);
  const noon = new Date(`${date}T12:00:00Z`);
  const eventTicker = eventTickerForDate(station.kalshiSeries, noon, station.timezone);
  const detail = await getEvent(eventTicker);
  const markets = detail.event.markets ?? [];
  const source = detail.event.settlement_sources?.[0];
  await pool.query(
    `INSERT INTO market_events
      (event_ticker, series_ticker, station_code, trade_date, title,
       settlement_source_name, settlement_source_url, raw_payload)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
     ON CONFLICT (event_ticker) DO UPDATE SET
      title=EXCLUDED.title,
      settlement_source_name=EXCLUDED.settlement_source_name,
      settlement_source_url=EXCLUDED.settlement_source_url,
      raw_payload=EXCLUDED.raw_payload`,
    [eventTicker, station.kalshiSeries, station.station, date, detail.event.title,
     source?.name ?? null, source?.url ?? null, detail.event],
  );

  const windowStart = new Date(noon.getTime() - 18 * 60 * 60 * 1000);
  const windowEnd = new Date(noon.getTime() + 30 * 60 * 60 * 1000);
  let quotes = 0;
  let trades = 0;
  for (const market of markets) {
    const band = marketToBand(market);
    await pool.query(
      `INSERT INTO market_contracts
        (ticker, event_ticker, label, lower_bound_f, upper_bound_f, result, raw_payload)
       VALUES ($1,$2,$3,$4,$5,$6,$7)
       ON CONFLICT (ticker) DO UPDATE SET
        label=EXCLUDED.label, lower_bound_f=EXCLUDED.lower_bound_f,
        upper_bound_f=EXCLUDED.upper_bound_f, result=EXCLUDED.result,
        raw_payload=EXCLUDED.raw_payload`,
      [band.ticker, eventTicker, band.label, band.lower, band.upper, band.result, market],
    );
    const candles = await getMinuteCandles(station.kalshiSeries, market.ticker, windowStart, windowEnd);
    for (const quote of candles) {
      await pool.query(
        `INSERT INTO market_quotes
          (contract_ticker, captured_at, yes_bid, yes_ask, last_price,
           yes_bid_open, yes_bid_low, yes_bid_high, yes_ask_open, yes_ask_low, yes_ask_high,
           last_price_open, last_price_low, last_price_high)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
         ON CONFLICT (contract_ticker, captured_at) DO UPDATE SET
          yes_bid=EXCLUDED.yes_bid, yes_ask=EXCLUDED.yes_ask,
          last_price=EXCLUDED.last_price,
          yes_bid_open=EXCLUDED.yes_bid_open, yes_bid_low=EXCLUDED.yes_bid_low,
          yes_bid_high=EXCLUDED.yes_bid_high, yes_ask_open=EXCLUDED.yes_ask_open,
          yes_ask_low=EXCLUDED.yes_ask_low, yes_ask_high=EXCLUDED.yes_ask_high,
          last_price_open=EXCLUDED.last_price_open, last_price_low=EXCLUDED.last_price_low,
          last_price_high=EXCLUDED.last_price_high`,
        [quote.contractTicker, quote.capturedAt, quote.yesBid, quote.yesAsk, quote.lastPrice ?? null,
         quote.yesBidOpen ?? null, quote.yesBidLow ?? null, quote.yesBidHigh ?? null,
         quote.yesAskOpen ?? null, quote.yesAskLow ?? null, quote.yesAskHigh ?? null,
         quote.lastPriceOpen ?? null, quote.lastPriceLow ?? null, quote.lastPriceHigh ?? null],
      );
    }
    quotes += candles.length;

    const tape = await getMarketTrades(market.ticker, windowStart, windowEnd);
    for (const trade of tape) {
      await pool.query(
        `INSERT INTO market_trades
          (trade_id, contract_ticker, created_at, yes_price, no_price, quantity,
           taker_outcome_side, taker_book_side, is_block_trade, raw_payload)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
         ON CONFLICT (trade_id) DO NOTHING`,
        [trade.tradeId, trade.contractTicker, trade.createdAt, trade.yesPrice, trade.noPrice,
         trade.quantity, trade.takerOutcomeSide, trade.takerBookSide, trade.isBlockTrade, trade],
      );
    }
    trades += tape.length;
  }

  const weatherAsOf = new Date(Math.min(windowEnd.getTime(), Date.now()));
  const ageDays = (Date.now() - weatherAsOf.getTime()) / 86_400_000;
  let observations: ObservationPoint[] = [];
  if (ageDays <= 30) {
    const reports = await getMetars(station.station, weatherAsOf, 48);
    observations = reports.filter((point) => isLocalDate(point.observedAt, date, station.timezone));
  } else if (station.iemNetwork) {
    observations = await getHighFrequencyArchive(
      station.station,
      station.iemNetwork,
      date,
      discoveredAt,
    );
  }
  await saveObservations(observations);
  return {
    station: station.station,
    date,
    eventTicker,
    contracts: markets.length,
    quotes,
    trades,
    observations: observations.length,
    actualReceiptObservations: observations.filter((point) => point.receiptQuality === "actual").length,
    discoveryOnlyObservations: observations.filter((point) => point.receiptQuality === "discovery_only").length,
  };
}
