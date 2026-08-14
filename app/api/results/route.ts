import { NextResponse } from "next/server";
import { pool } from "@/lib/db";

export const dynamic = "force-dynamic";

export async function GET() {
  if (!pool) return NextResponse.json({ mode: "verified_demo", latestRun: null, stations: [], coverage: [] });
  const latest = await pool.query<{
    id: string; model_version: string; started_at: Date; finished_at: Date | null;
    as_of_start: Date; as_of_end: Date; status: string; summary: Record<string, unknown>;
  }>(
    `SELECT id, model_version, started_at, finished_at, as_of_start, as_of_end, status, summary
       FROM backtest_runs WHERE status='success' ORDER BY finished_at DESC LIMIT 1`,
  );
  const run = latest.rows[0];
  const stationResults = run ? await pool.query<{
    station_code: string; signals: string; executable: string; resolved: string;
    wins: string; avg_lag: string | null; gross: string | null; net: string | null;
  }>(
    `SELECT station_code,
            count(*)::text AS signals,
            count(*) FILTER (WHERE executable_proxy)::text AS executable,
            count(*) FILTER (WHERE outcome IS NOT NULL)::text AS resolved,
            count(*) FILTER (WHERE outcome='win')::text AS wins,
            avg(reaction_lag_seconds)::text AS avg_lag,
            sum(gross_profit_per_contract)::text AS gross,
            sum(net_profit_per_contract)::text AS net
       FROM backtest_signals WHERE run_id=$1 GROUP BY station_code ORDER BY station_code`,
    [run.id],
  ) : { rows: [] };
  const coverage = await pool.query<{
    station_code: string; first_observation: Date | null; last_observation: Date | null;
    observations: string; actual_receipts: string; discovery_only: string;
    market_days: string; quotes: string;
  }>(
    `WITH observation_coverage AS (
       SELECT station_code, min(observed_at) AS first_observation,
              max(observed_at) AS last_observation, count(*) AS observations,
              count(*) FILTER (WHERE receipt_quality='actual') AS actual_receipts,
              count(*) FILTER (WHERE receipt_quality='discovery_only') AS discovery_only
         FROM weather_observations GROUP BY station_code
     ), market_coverage AS (
       SELECT station_code, count(*) AS market_days
         FROM market_events GROUP BY station_code
     ), quote_coverage AS (
       SELECT e.station_code, count(q.*) AS quotes
         FROM market_events e
         JOIN market_contracts c USING (event_ticker)
         JOIN market_quotes q ON q.contract_ticker=c.ticker
        GROUP BY e.station_code
     )
     SELECT s.station_code, o.first_observation, o.last_observation,
            COALESCE(o.observations,0)::text AS observations,
            COALESCE(o.actual_receipts,0)::text AS actual_receipts,
            COALESCE(o.discovery_only,0)::text AS discovery_only,
            COALESCE(m.market_days,0)::text AS market_days,
            COALESCE(q.quotes,0)::text AS quotes
       FROM stations s
       LEFT JOIN observation_coverage o USING (station_code)
       LEFT JOIN market_coverage m USING (station_code)
       LEFT JOIN quote_coverage q USING (station_code)
      ORDER BY s.station_code`,
  );
  return NextResponse.json({
    mode: "database",
    generatedAt: new Date().toISOString(),
    latestRun: run ? {
      id: Number(run.id),
      modelVersion: run.model_version,
      startedAt: run.started_at.toISOString(),
      finishedAt: run.finished_at?.toISOString() ?? null,
      from: run.as_of_start.toISOString(),
      to: run.as_of_end.toISOString(),
      status: run.status,
      summary: run.summary,
    } : null,
    stations: stationResults.rows.map((row) => ({
      station: row.station_code,
      signals: Number(row.signals),
      executable: Number(row.executable),
      resolved: Number(row.resolved),
      wins: Number(row.wins),
      winRate: Number(row.resolved) ? Number(row.wins) / Number(row.resolved) : null,
      averageLagSeconds: row.avg_lag === null ? null : Number(row.avg_lag),
      grossProfit: row.gross === null ? null : Number(row.gross),
      netProfit: row.net === null ? null : Number(row.net),
    })),
    coverage: coverage.rows.map((row) => ({
      station: row.station_code,
      firstObservation: row.first_observation?.toISOString() ?? null,
      lastObservation: row.last_observation?.toISOString() ?? null,
      observations: Number(row.observations),
      actualReceipts: Number(row.actual_receipts),
      discoveryOnly: Number(row.discovery_only),
      marketDays: Number(row.market_days),
      quotes: Number(row.quotes),
    })),
  });
}
