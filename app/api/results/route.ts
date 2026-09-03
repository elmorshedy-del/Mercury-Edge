import { NextResponse } from "next/server";
import { pool } from "@/lib/db";

export const dynamic = "force-dynamic";

function evidenceBoolean(evidence: Record<string, unknown>, key: string) {
  return evidence[key] === true;
}

function evidenceNumber(evidence: Record<string, unknown>, key: string) {
  const value = evidence[key];
  if (value === null || value === undefined) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function evidenceString(evidence: Record<string, unknown>, key: string) {
  const value = evidence[key];
  return typeof value === "string" ? value : null;
}

export async function GET() {
  if (!pool) return NextResponse.json({ mode: "verified_demo", latestRun: null, stations: [], signals: [], coverage: [] });
  const latest = await pool.query<{
    id: string; model_version: string; started_at: Date; finished_at: Date | null;
    as_of_start: Date; as_of_end: Date; status: string; summary: Record<string, unknown>;
  }>(
    `SELECT id, model_version, started_at, finished_at, as_of_start, as_of_end, status, summary
       FROM backtest_runs WHERE status='success' ORDER BY finished_at DESC LIMIT 1`,
  );
  const run = latest.rows[0];
  const stationResults = run ? await pool.query<{
    station_code: string; signals: string; violent: string; hard_state: string;
    candidate_proxy: string; tape_observed: string; l2_simulated: string;
    proxy_resolved: string; proxy_wins: string; avg_lag: string | null;
    proxy_net: string | null; tape_counterfactual_net: string | null;
  }>(
    `SELECT station_code,
            count(*)::text AS signals,
            count(*) FILTER (WHERE COALESCE((evidence->>'violent')::boolean,false))::text AS violent,
            count(*) FILTER (WHERE COALESCE((evidence->>'hardStateProven')::boolean,false))::text AS hard_state,
            count(*) FILTER (WHERE COALESCE((evidence->>'candidateProxy')::boolean,false))::text AS candidate_proxy,
            count(*) FILTER (WHERE evidence->>'evidenceTier'='trade_tape_observed')::text AS tape_observed,
            count(*) FILTER (WHERE executable_proxy)::text AS l2_simulated,
            count(*) FILTER (WHERE COALESCE((evidence->>'candidateProxy')::boolean,false)
                              AND outcome IS NOT NULL)::text AS proxy_resolved,
            count(*) FILTER (WHERE COALESCE((evidence->>'candidateProxy')::boolean,false)
                              AND outcome='win')::text AS proxy_wins,
            avg(reaction_lag_seconds)::text AS avg_lag,
            sum(net_profit_per_contract) FILTER (
              WHERE COALESCE((evidence->>'candidateProxy')::boolean,false)
            )::text AS proxy_net,
            sum((evidence->>'tapeCounterfactualNetProfit')::numeric) FILTER (
              WHERE evidence->>'tapeCounterfactualNetProfit' IS NOT NULL
            )::text AS tape_counterfactual_net
       FROM backtest_signals WHERE run_id=$1 GROUP BY station_code ORDER BY station_code`,
    [run.id],
  ) : { rows: [] };
  const signalRows = run ? await pool.query<{
    id: string; station_code: string; trade_date: string; label: string;
    triggered_at: Date; entry_price: string | null; reaction_lag_seconds: number | null;
    executable_proxy: boolean; outcome: string | null; net_profit_per_contract: string | null;
    limitation: string | null; evidence: Record<string, unknown>;
  }>(
    `SELECT bs.id, bs.station_code, e.trade_date::text, mc.label,
            bs.triggered_at, bs.entry_price, bs.reaction_lag_seconds,
            bs.executable_proxy, bs.outcome, bs.net_profit_per_contract,
            bs.limitation, bs.evidence
       FROM backtest_signals bs
       JOIN market_events e ON e.event_ticker=bs.event_ticker
       JOIN market_contracts mc ON mc.ticker=bs.contract_ticker
      WHERE bs.run_id=$1
      ORDER BY COALESCE((bs.evidence->>'violent')::boolean,false) DESC,
               COALESCE((bs.evidence->>'violentMoveCents')::numeric,-1) DESC,
               bs.triggered_at DESC, bs.id DESC`,
    [run.id],
  ) : { rows: [] };
  const coverage = await pool.query<{
    station_code: string; first_observation: Date | null; last_observation: Date | null;
    observations: string; actual_receipts: string; discovery_only: string;
    market_days: string; quotes: string; trades: string;
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
     ), trade_coverage AS (
       SELECT e.station_code, count(t.*) AS trades
         FROM market_events e
         JOIN market_contracts c USING (event_ticker)
         JOIN market_trades t ON t.contract_ticker=c.ticker
        GROUP BY e.station_code
     )
     SELECT s.station_code, o.first_observation, o.last_observation,
            COALESCE(o.observations,0)::text AS observations,
            COALESCE(o.actual_receipts,0)::text AS actual_receipts,
            COALESCE(o.discovery_only,0)::text AS discovery_only,
            COALESCE(m.market_days,0)::text AS market_days,
            COALESCE(q.quotes,0)::text AS quotes,
            COALESCE(t.trades,0)::text AS trades
       FROM stations s
       LEFT JOIN observation_coverage o USING (station_code)
       LEFT JOIN market_coverage m USING (station_code)
       LEFT JOIN quote_coverage q USING (station_code)
       LEFT JOIN trade_coverage t USING (station_code)
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
      violent: Number(row.violent),
      hardState: Number(row.hard_state),
      candidateProxy: Number(row.candidate_proxy),
      tapeObserved: Number(row.tape_observed),
      l2Simulated: Number(row.l2_simulated),
      proxyResolved: Number(row.proxy_resolved),
      proxyWins: Number(row.proxy_wins),
      proxyWinRate: Number(row.proxy_resolved) ? Number(row.proxy_wins) / Number(row.proxy_resolved) : null,
      averageLagSeconds: row.avg_lag === null ? null : Number(row.avg_lag),
      proxyNetProfit: row.proxy_net === null ? null : Number(row.proxy_net),
      tapeCounterfactualNetProfit: row.tape_counterfactual_net === null ? null : Number(row.tape_counterfactual_net),
    })),
    signals: signalRows.rows.map((row) => {
      const evidence = row.evidence ?? {};
      return {
        id: Number(row.id),
        station: row.station_code,
        date: row.trade_date.slice(0, 10),
        contract: row.label,
        triggeredAt: row.triggered_at.toISOString(),
        entryCents: row.entry_price === null ? null : Number(row.entry_price) * 100,
        reactionLagSeconds: row.reaction_lag_seconds,
        reactionLagLowerSeconds: evidenceNumber(evidence, "reactionLagLowerSeconds"),
        reactionLagUpperSeconds: evidenceNumber(evidence, "reactionLagUpperSeconds"),
        triggerSource: evidenceString(evidence, "triggerSource"),
        triggerReportType: evidenceString(evidence, "triggerReportType"),
        triggerKind: evidenceString(evidence, "triggerKind"),
        triggerReceiptQuality: evidenceString(evidence, "triggerReceiptQuality"),
        triggerRawText: evidenceString(evidence, "triggerRawText"),
        hardStateProven: evidenceBoolean(evidence, "hardStateProven"),
        candidateProxy: evidenceBoolean(evidence, "candidateProxy"),
        evidenceTier: evidenceString(evidence, "evidenceTier") ?? "weather_only",
        violent: evidenceBoolean(evidence, "violent"),
        violentMoveCents: evidenceNumber(evidence, "violentMoveCents"),
        staleEdgeCents: evidenceNumber(evidence, "staleEdgeCents"),
        observedNoTakerQuantity: evidenceNumber(evidence, "observedNoTakerQuantity") ?? 0,
        observedNoTakerVwap: evidenceNumber(evidence, "observedNoTakerVwap"),
        tapeCounterfactualNetProfit: evidenceNumber(evidence, "tapeCounterfactualNetProfit"),
        profitStatus: evidenceString(evidence, "profitStatus") ?? "none",
        executable: row.executable_proxy,
        outcome: row.outcome,
        proxyNetProfit: row.net_profit_per_contract === null ? null : Number(row.net_profit_per_contract),
        limitation: row.limitation,
      };
    }),
    coverage: coverage.rows.map((row) => ({
      station: row.station_code,
      firstObservation: row.first_observation?.toISOString() ?? null,
      lastObservation: row.last_observation?.toISOString() ?? null,
      observations: Number(row.observations),
      actualReceipts: Number(row.actual_receipts),
      discoveryOnly: Number(row.discovery_only),
      marketDays: Number(row.market_days),
      quotes: Number(row.quotes),
      trades: Number(row.trades),
    })),
  });
}
