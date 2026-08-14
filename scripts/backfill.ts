import { STATIONS, type StationConfig } from "../lib/config";
import { pool } from "../lib/db";
import { getMetars } from "../lib/sources/awc";
import { getHighFrequencyArchive } from "../lib/sources/iem";
import { getEvent, getMinuteCandles, marketToBand } from "../lib/sources/kalshi";
import { eachUtcDate, eventTickerForDate, isLocalDate } from "../lib/time";
import type { ObservationPoint } from "../lib/types";

function value(name: string) {
  return process.argv.find((arg) => arg.startsWith(`--${name}=`))?.split("=")[1];
}

function selection() {
  const requested = value("stations");
  if (!requested) return STATIONS;
  const wanted = new Set(requested.toUpperCase().split(","));
  return STATIONS.filter((station) => wanted.has(station.station));
}

async function seedStation(station: StationConfig) {
  await pool!.query(
    `INSERT INTO stations
      (station_code, city, timezone, kalshi_series, nws_location, iem_network)
     VALUES ($1,$2,$3,$4,$5,$6)
     ON CONFLICT (station_code) DO NOTHING`,
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
       ON CONFLICT (station_code, source, report_type, observed_at, raw_text) DO NOTHING`,
      [point.station, point.source, point.reportType, point.observedAt, point.receivedAt,
       point.receiptQuality, point.temperatureF, point.settlementCompatible,
       point.source === "NOAA_AWC" ? "0.1C source / exact conversion" : "0.1C MADIS archive",
       point.rawText ?? ""],
    );
  }
}

async function backfillDay(station: StationConfig, date: string) {
  await seedStation(station);
  const noon = new Date(`${date}T12:00:00Z`);
  const eventTicker = eventTickerForDate(station.kalshiSeries, noon, station.timezone);
  const detail = await getEvent(eventTicker);
  const markets = detail.event.markets ?? [];
  const source = detail.event.settlement_sources?.[0];
  await pool!.query(
    `INSERT INTO market_events
      (event_ticker, series_ticker, station_code, trade_date, title,
       settlement_source_name, settlement_source_url, raw_payload)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
     ON CONFLICT (event_ticker) DO UPDATE SET raw_payload = EXCLUDED.raw_payload`,
    [eventTicker, station.kalshiSeries, station.station, date, detail.event.title,
     source?.name ?? null, source?.url ?? null, detail.event],
  );

  const windowStart = new Date(noon.getTime() - 18 * 60 * 60 * 1000);
  const windowEnd = new Date(noon.getTime() + 30 * 60 * 60 * 1000);
  let quotes = 0;
  for (const market of markets) {
    const band = marketToBand(market);
    await pool!.query(
      `INSERT INTO market_contracts
        (ticker, event_ticker, label, lower_bound_f, upper_bound_f, result, raw_payload)
       VALUES ($1,$2,$3,$4,$5,$6,$7)
       ON CONFLICT (ticker) DO UPDATE SET result = EXCLUDED.result, raw_payload = EXCLUDED.raw_payload`,
      [band.ticker, eventTicker, band.label, band.lower, band.upper, band.result, market],
    );
    const candles = await getMinuteCandles(station.kalshiSeries, market.ticker, windowStart, windowEnd);
    for (const quote of candles) {
      await pool!.query(
        `INSERT INTO market_quotes
          (contract_ticker, captured_at, yes_bid, yes_ask, last_price)
         VALUES ($1,$2,$3,$4,$5)
         ON CONFLICT (contract_ticker, captured_at) DO UPDATE SET
          yes_bid=EXCLUDED.yes_bid, yes_ask=EXCLUDED.yes_ask, last_price=EXCLUDED.last_price`,
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
      new Date(),
    );
  }
  await saveObservations(observations);
  return { station: station.station, date, eventTicker, contracts: markets.length, quotes, observations: observations.length };
}

async function main() {
  if (!pool) throw new Error("DATABASE_URL is required for backfill");
  const from = value("from");
  const to = value("to") ?? from;
  if (!from || !to) {
    throw new Error("Usage: npm run backfill -- --from=YYYY-MM-DD --to=YYYY-MM-DD --stations=KNYC,KPHL");
  }
  const results = [];
  for (const date of eachUtcDate(from, to)) {
    for (const station of selection()) {
      try {
        const result = await backfillDay(station, date);
        results.push(result);
        console.log(JSON.stringify(result));
      } catch (error) {
        console.error(JSON.stringify({ station: station.station, date, error: error instanceof Error ? error.message : String(error) }));
      }
    }
  }
  console.log(JSON.stringify({ completed: results.length }, null, 2));
  await pool.end();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
