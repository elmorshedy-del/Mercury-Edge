import { NextResponse } from "next/server";
import { pool } from "@/lib/db";

export const dynamic = "force-dynamic";

const REACTED_YES_ASK_THRESHOLD = 0.05;
const ENTRY_TOLERANCE_SECONDS = 90;

export async function GET() {
  if (!pool) {
    return NextResponse.json({
      mode: "verified_demo",
      generatedAt: new Date().toISOString(),
      stations: [],
      signals: [],
    });
  }

  const stationSnapshot = await pool.query<{
    station_code: string;
    city: string;
    timezone: string;
    observed_at: Date | null;
    received_at: Date | null;
    temperature_f: string | null;
    running_high_f: string | null;
    event_ticker: string | null;
    latest_quote_at: Date | null;
  }>(
    `SELECT s.station_code, s.city, s.timezone,
            obs.observed_at, obs.received_at, obs.temperature_f,
            high.running_high_f, evt.event_ticker, quotes.latest_quote_at
       FROM stations s
       LEFT JOIN LATERAL (
         SELECT o.observed_at, o.received_at, o.temperature_f
           FROM weather_observations o
          WHERE o.station_code=s.station_code
            AND o.temperature_f IS NOT NULL
            AND (o.observed_at AT TIME ZONE s.timezone)::date =
                (now() AT TIME ZONE s.timezone)::date
          ORDER BY o.received_at DESC
          LIMIT 1
       ) obs ON true
       LEFT JOIN LATERAL (
         SELECT max(o.temperature_f) AS running_high_f
           FROM weather_observations o
          WHERE o.station_code=s.station_code
            AND o.temperature_f IS NOT NULL
            AND o.settlement_compatible
            AND (o.observed_at AT TIME ZONE s.timezone)::date =
                (now() AT TIME ZONE s.timezone)::date
       ) high ON true
       LEFT JOIN LATERAL (
         SELECT e.event_ticker
           FROM market_events e
          WHERE e.station_code=s.station_code
            AND e.trade_date=(now() AT TIME ZONE s.timezone)::date
          ORDER BY e.discovered_at DESC
          LIMIT 1
       ) evt ON true
       LEFT JOIN LATERAL (
         SELECT max(q.captured_at) AS latest_quote_at
           FROM market_contracts c
           JOIN market_quotes q ON q.contract_ticker=c.ticker
          WHERE c.event_ticker=evt.event_ticker
       ) quotes ON true
      WHERE s.active
      ORDER BY s.station_code`,
  );

  const liveSignals = await pool.query<{
    station_code: string;
    city: string;
    timezone: string;
    event_ticker: string;
    contract_ticker: string;
    label: string;
    upper_bound_f: string;
    trigger_observed_at: Date;
    triggered_at: Date;
    receipt_quality: string;
    trigger_temperature_f: string;
    running_high_f: string;
    entry_captured_at: Date | null;
    entry_yes_bid: string | null;
    entry_yes_ask: string | null;
    reaction_at: Date | null;
    reaction_lag_seconds: string | null;
    latest_quote_at: Date | null;
    latest_yes_bid: string | null;
    latest_yes_ask: string | null;
    latest_last_price: string | null;
  }>(
    `WITH current_events AS (
       SELECT e.event_ticker, e.station_code, s.city, s.timezone
         FROM market_events e
         JOIN stations s ON s.station_code=e.station_code
        WHERE s.active
          AND e.trade_date=(now() AT TIME ZONE s.timezone)::date
     )
     SELECT e.station_code, e.city, e.timezone, e.event_ticker,
            c.ticker AS contract_ticker, c.label, c.upper_bound_f::text,
            trigger.observed_at AS trigger_observed_at,
            trigger.received_at AS triggered_at,
            trigger.receipt_quality,
            trigger.temperature_f::text AS trigger_temperature_f,
            current_high.running_high_f::text,
            entry.captured_at AS entry_captured_at,
            entry.yes_bid::text AS entry_yes_bid,
            entry.yes_ask::text AS entry_yes_ask,
            reaction.captured_at AS reaction_at,
            CASE WHEN reaction.captured_at IS NULL THEN NULL
                 ELSE extract(epoch FROM (reaction.captured_at-trigger.received_at))::text END AS reaction_lag_seconds,
            latest.captured_at AS latest_quote_at,
            latest.yes_bid::text AS latest_yes_bid,
            latest.yes_ask::text AS latest_yes_ask,
            latest.last_price::text AS latest_last_price
       FROM current_events e
       JOIN market_contracts c ON c.event_ticker=e.event_ticker
       JOIN LATERAL (
         SELECT o.observed_at, o.received_at, o.receipt_quality, o.temperature_f
           FROM weather_observations o
          WHERE o.station_code=e.station_code
            AND o.temperature_f IS NOT NULL
            AND o.settlement_compatible
            AND (o.observed_at AT TIME ZONE e.timezone)::date =
                (now() AT TIME ZONE e.timezone)::date
            AND c.upper_bound_f IS NOT NULL
            AND c.upper_bound_f < round(o.temperature_f)
          ORDER BY o.received_at ASC
          LIMIT 1
       ) trigger ON true
       JOIN LATERAL (
         SELECT max(o.temperature_f) AS running_high_f
           FROM weather_observations o
          WHERE o.station_code=e.station_code
            AND o.temperature_f IS NOT NULL
            AND o.settlement_compatible
            AND (o.observed_at AT TIME ZONE e.timezone)::date =
                (now() AT TIME ZONE e.timezone)::date
       ) current_high ON true
       LEFT JOIN LATERAL (
         SELECT q.captured_at, q.yes_bid, q.yes_ask
           FROM market_quotes q
          WHERE q.contract_ticker=c.ticker
            AND q.captured_at >= trigger.received_at
            AND q.captured_at <= trigger.received_at + make_interval(secs => $1)
          ORDER BY q.captured_at ASC
          LIMIT 1
       ) entry ON true
       LEFT JOIN LATERAL (
         SELECT q.captured_at
           FROM market_quotes q
          WHERE q.contract_ticker=c.ticker
            AND q.captured_at >= trigger.received_at
            AND q.yes_ask IS NOT NULL
            AND q.yes_ask <= $2
          ORDER BY q.captured_at ASC
          LIMIT 1
       ) reaction ON true
       LEFT JOIN LATERAL (
         SELECT q.captured_at, q.yes_bid, q.yes_ask, q.last_price
           FROM market_quotes q
          WHERE q.contract_ticker=c.ticker
          ORDER BY q.captured_at DESC
          LIMIT 1
       ) latest ON true
      ORDER BY trigger.received_at DESC, e.station_code, c.upper_bound_f`,
    [ENTRY_TOLERANCE_SECONDS, REACTED_YES_ASK_THRESHOLD],
  );

  const signals = liveSignals.rows.map((row) => {
    const latestYesAsk = row.latest_yes_ask === null ? null : Number(row.latest_yes_ask);
    const entryYesBid = row.entry_yes_bid === null ? null : Number(row.entry_yes_bid);
    const reactionLag = row.reaction_lag_seconds === null ? null : Math.round(Number(row.reaction_lag_seconds));
    const status = row.reaction_at
      ? "repriced"
      : latestYesAsk !== null && latestYesAsk > REACTED_YES_ASK_THRESHOLD
        ? "open_gap"
        : "awaiting_quote";

    return {
      station: row.station_code,
      city: row.city,
      timezone: row.timezone,
      eventTicker: row.event_ticker,
      contractTicker: row.contract_ticker,
      label: row.label,
      upperBoundF: Number(row.upper_bound_f),
      triggerObservedAt: row.trigger_observed_at.toISOString(),
      triggeredAt: row.triggered_at.toISOString(),
      receiptQuality: row.receipt_quality,
      triggerTemperatureF: Number(row.trigger_temperature_f),
      runningHighF: Number(row.running_high_f),
      officialFloorF: Math.round(Number(row.running_high_f)),
      entryCapturedAt: row.entry_captured_at?.toISOString() ?? null,
      entryYesBidCents: row.entry_yes_bid === null ? null : Number(row.entry_yes_bid) * 100,
      entryYesAskCents: row.entry_yes_ask === null ? null : Number(row.entry_yes_ask) * 100,
      noAskProxyCents: entryYesBid === null ? null : (1 - entryYesBid) * 100,
      reactionAt: row.reaction_at?.toISOString() ?? null,
      reactionLagSeconds: reactionLag,
      latestQuoteAt: row.latest_quote_at?.toISOString() ?? null,
      latestYesBidCents: row.latest_yes_bid === null ? null : Number(row.latest_yes_bid) * 100,
      latestYesAskCents: latestYesAsk === null ? null : latestYesAsk * 100,
      latestLastPriceCents: row.latest_last_price === null ? null : Number(row.latest_last_price) * 100,
      executableProxy: Boolean(row.entry_captured_at && row.receipt_quality === "actual"),
      status,
    };
  });

  return NextResponse.json({
    mode: "database",
    generatedAt: new Date().toISOString(),
    stations: stationSnapshot.rows.map((row) => ({
      station: row.station_code,
      city: row.city,
      timezone: row.timezone,
      observedAt: row.observed_at?.toISOString() ?? null,
      receivedAt: row.received_at?.toISOString() ?? null,
      temperatureF: row.temperature_f === null ? null : Number(row.temperature_f),
      runningHighF: row.running_high_f === null ? null : Number(row.running_high_f),
      officialFloorF: row.running_high_f === null ? null : Math.round(Number(row.running_high_f)),
      eventTicker: row.event_ticker,
      latestQuoteAt: row.latest_quote_at?.toISOString() ?? null,
    })),
    signals,
  });
}
