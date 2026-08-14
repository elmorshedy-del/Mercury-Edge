import type { StationConfig } from "../config";
import { pool } from "../db";
import { getMetars } from "../sources/awc";
import { getHighFrequencyArchive } from "../sources/iem";
import { getEvent, getMinuteCandles, marketToBand } from "../sources/kalshi";
import { eventTickerForDate, isLocalDate } from "../time";
import type { ObservationPoint } from "../types";

export type BackfillDayResult = {
  station: string;
  date: string;
  eventTicker: string;
  contracts: number;
  quotes: number;
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
         temperature_f, settlement_compatible, source_precision, raw_text)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
       ON CONFLICT (station_code, source, report_type, observed_at, raw_text) DO UPDATE SET
        received_at=EXCLUDED.received_at,
        receipt_quality=EXCLUDED.receipt_quality,
        temperature_f=EXCLUDED.temperature_f,
        settlement_compatible=EXCLUDED.settlement_compatible`,
      [point.station, point.source, point.reportType, point.observedAt, point.receivedAt,
       point.receiptQuality, point.temperatureF, point.settlementCompatible,
       point.source === "NOAA_AWC" ? "0.1C source / exact conversion" : "0.1C MADIS archive",
       point.rawText ?? ""],
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
          (contract_ticker, captured_at, yes_bid, yes_ask, last_price)
         VALUES ($1,$2,$3,$4,$5)
         ON CONFLICT (contract_ticker, captured_at) DO UPDATE SET
          yes_bid=EXCLUDED.yes_bid, yes_ask=EXCLUDED.yes_ask,
          last_price=EXCLUDED.last_price`,
        [quote.contractTicker, quote.capturedAt, quote.yesBid, quote.yesAsk, quote.lastPrice ?? null],
      );
    }
    quotes += candles.length;
  }

  const ageDays = (Date.now() - windowEnd.getTime()) / 86_400_000;
  let observations: ObservationPoint[] = [];
  if (ageDays <= 15) {
    const reports = await getMetars(station.station, windowEnd, 48);
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
    observations: observations.length,
    actualReceiptObservations: observations.filter((point) => point.receiptQuality === "actual").length,
    discoveryOnlyObservations: observations.filter((point) => point.receiptQuality === "discovery_only").length,
  };
}
