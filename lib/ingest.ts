import type { PoolClient } from "pg";
import { STATIONS, type StationConfig } from "./config";
import { pool, transaction } from "./db";
import { getMetars } from "./sources/awc";
import { getEvent, getMinuteCandles, marketToBand } from "./sources/kalshi";
import { getProducts } from "./sources/nws";
import { eventTickerForDate, localDate } from "./time";

export type IngestResult = {
  station: string;
  startedAt: string;
  finishedAt: string;
  observations: number;
  products: number;
  events: number;
  contracts: number;
  quotes: number;
  errors: string[];
};

async function upsertStation(client: PoolClient, station: StationConfig) {
  await client.query(
    `INSERT INTO stations
      (station_code, city, timezone, kalshi_series, nws_location, iem_network)
     VALUES ($1, $2, $3, $4, $5, $6)
     ON CONFLICT (station_code) DO UPDATE SET
      city = EXCLUDED.city, timezone = EXCLUDED.timezone,
      kalshi_series = EXCLUDED.kalshi_series,
      nws_location = EXCLUDED.nws_location, iem_network = EXCLUDED.iem_network`,
    [station.station, station.city, station.timezone, station.kalshiSeries, station.nwsLocation, station.iemNetwork],
  );
}

async function markHealth(
  client: PoolClient,
  source: string,
  station: string,
  ok: boolean,
  lastRecordAt: string | null,
  error: string | null,
) {
  await client.query(
    `INSERT INTO source_health
      (source, station_code, last_success_at, last_record_at, last_error_at, last_error, consecutive_failures)
     VALUES ($1, $2, CASE WHEN $3 THEN now() END, $4,
       CASE WHEN $3 THEN NULL ELSE now() END, $5, CASE WHEN $3 THEN 0 ELSE 1 END)
     ON CONFLICT (source, station_code) DO UPDATE SET
       last_success_at = CASE WHEN $3 THEN now() ELSE source_health.last_success_at END,
       last_record_at = COALESCE($4, source_health.last_record_at),
       last_error_at = CASE WHEN $3 THEN source_health.last_error_at ELSE now() END,
       last_error = CASE WHEN $3 THEN NULL ELSE $5 END,
       consecutive_failures = CASE WHEN $3 THEN 0 ELSE source_health.consecutive_failures + 1 END,
       updated_at = now()`,
    [source, station, ok, lastRecordAt, error],
  );
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

export async function ingestStation(station: StationConfig, at = new Date()): Promise<IngestResult> {
  if (!pool) throw new Error("DATABASE_URL is not configured");
  const startedAt = new Date().toISOString();
  const result: IngestResult = {
    station: station.station,
    startedAt,
    finishedAt: startedAt,
    observations: 0,
    products: 0,
    events: 0,
    contracts: 0,
    quotes: 0,
    errors: [],
  };

  await transaction((client) => upsertStation(client, station));

  try {
    const observations = await getMetars(station.station, at, 6);
    await transaction(async (client) => {
      for (const point of observations) {
        await client.query(
          `INSERT INTO weather_observations
            (station_code, source, report_type, observed_at, received_at, receipt_quality,
             temperature_f, dewpoint_f, wind_direction_deg, wind_speed_kt, pressure_hpa,
             sky_cover, precipitation_in, settlement_compatible, source_precision,
             raw_text, raw_payload)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
           ON CONFLICT (station_code, source, report_type, observed_at, raw_text) DO NOTHING`,
          [
            point.station, point.source, point.reportType, point.observedAt, point.receivedAt,
            point.receiptQuality, point.temperatureF, point.dewpointF,
            typeof point.windDirection === "number" ? point.windDirection : null,
            point.windSpeedKt, point.pressureHpa, point.skyCover, point.precipitationIn,
            point.settlementCompatible, "0.1C source / exact conversion", point.rawText, point.payload,
          ],
        );
      }
      await markHealth(
        client,
        "NOAA_AWC",
        station.station,
        true,
        observations.at(-1)?.receivedAt ?? null,
        null,
      );
    });
    result.observations = observations.length;
  } catch (error) {
    const message = `NOAA AWC: ${errorMessage(error)}`;
    result.errors.push(message);
    await transaction((client) => markHealth(client, "NOAA_AWC", station.station, false, null, message));
  }

  if (station.nwsLocation) {
    for (const productType of ["DSM", "CLI"] as const) {
      try {
        const products = await getProducts(productType, station.nwsLocation, station.timezone, 4);
        await transaction(async (client) => {
          for (const product of products) {
            await client.query(
              `INSERT INTO product_releases
                (product_id, station_code, product_type, issued_at, discovered_at,
                 reported_high_f, high_observed_at, source_url, raw_text, raw_payload)
               VALUES ($1,$2,$3,$4,now(),$5,$6,$7,$8,$9)
               ON CONFLICT (product_id) DO NOTHING`,
              [product.productId, station.station, product.productType, product.issuedAt,
               product.reportedHighF, product.highObservedAt, product.sourceUrl,
               product.rawText, product.payload],
            );
          }
          await markHealth(
            client,
            `NWS_${productType}`,
            station.station,
            true,
            products[0]?.issuedAt ?? null,
            null,
          );
        });
        result.products += products.length;
      } catch (error) {
        const message = `NWS ${productType}: ${errorMessage(error)}`;
        result.errors.push(message);
        await transaction((client) => markHealth(client, `NWS_${productType}`, station.station, false, null, message));
      }
    }
  }

  try {
    const eventTicker = eventTickerForDate(station.kalshiSeries, at, station.timezone);
    const detail = await getEvent(eventTicker);
    const markets = detail.event.markets ?? [];
    const tradeDate = localDate(at, station.timezone);
    const source = detail.event.settlement_sources?.[0];
    await transaction(async (client) => {
      await client.query(
        `INSERT INTO market_events
          (event_ticker, series_ticker, station_code, trade_date, title,
           settlement_source_name, settlement_source_url, raw_payload)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
         ON CONFLICT (event_ticker) DO UPDATE SET raw_payload = EXCLUDED.raw_payload`,
        [eventTicker, station.kalshiSeries, station.station, tradeDate, detail.event.title,
         source?.name ?? null, source?.url ?? null, detail.event],
      );
      for (const market of markets) {
        const band = marketToBand(market);
        await client.query(
          `INSERT INTO market_contracts
            (ticker, event_ticker, label, lower_bound_f, upper_bound_f, result, raw_payload)
           VALUES ($1,$2,$3,$4,$5,$6,$7)
           ON CONFLICT (ticker) DO UPDATE SET
            result = EXCLUDED.result, raw_payload = EXCLUDED.raw_payload`,
          [band.ticker, eventTicker, band.label, band.lower, band.upper, band.result, market],
        );
      }
      await markHealth(client, "KALSHI", station.station, true, new Date().toISOString(), null);
    });
    result.events = 1;
    result.contracts = markets.length;

    const candleStart = new Date(at.getTime() - 6 * 60 * 60 * 1000);
    for (const market of markets) {
      const candles = await getMinuteCandles(station.kalshiSeries, market.ticker, candleStart, at);
      await transaction(async (client) => {
        for (const quote of candles) {
          await client.query(
            `INSERT INTO market_quotes
              (contract_ticker, captured_at, yes_bid, yes_ask, last_price, bid_size, ask_size)
             VALUES ($1,$2,$3,$4,$5,$6,$7)
             ON CONFLICT (contract_ticker, captured_at) DO UPDATE SET
              yes_bid = EXCLUDED.yes_bid, yes_ask = EXCLUDED.yes_ask,
              last_price = EXCLUDED.last_price`,
            [quote.contractTicker, quote.capturedAt, quote.yesBid, quote.yesAsk,
             quote.lastPrice ?? null, quote.bidSize ?? null, quote.askSize ?? null],
          );
        }
      });
      result.quotes += candles.length;
    }
  } catch (error) {
    const message = `Kalshi: ${errorMessage(error)}`;
    result.errors.push(message);
    await transaction((client) => markHealth(client, "KALSHI", station.station, false, null, message));
  }

  result.finishedAt = new Date().toISOString();
  await pool.query(
    `INSERT INTO ingestion_runs
      (started_at, finished_at, source, station_code, status, records_written, error_message, metadata)
     VALUES ($1,$2,'station_bundle',$3,$4,$5,$6,$7)`,
    [result.startedAt, result.finishedAt, station.station,
     result.errors.length ? "partial" : "success",
     result.observations + result.products + result.events + result.contracts + result.quotes,
     result.errors.join(" | ") || null, result],
  );
  return result;
}

export async function ingestAll(at = new Date(), stations = STATIONS) {
  const results: IngestResult[] = [];
  for (const station of stations) results.push(await ingestStation(station, at));
  return results;
}
