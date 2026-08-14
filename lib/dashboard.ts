import { STATIONS } from "./config";
import { hasDatabase, pool } from "./db";
import { demoDashboard } from "./demo";
import { secondsBetween } from "./time";
import type { CaseStudy, CitySummary, DashboardPayload } from "./types";

function median(values: number[]) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

function latencyBuckets(values: number[]) {
  const buckets = [
    { label: "≤1m", min: -Infinity, max: 60 },
    { label: "1–3m", min: 60, max: 180 },
    { label: "3–5m", min: 180, max: 300 },
    { label: "5–15m", min: 300, max: 900 },
    { label: ">15m", min: 900, max: Infinity },
  ];
  return buckets.map((bucket) => ({
    label: bucket.label,
    count: values.filter((value) => value > bucket.min && value <= bucket.max).length,
  }));
}

export async function getDashboard(): Promise<DashboardPayload> {
  if (!hasDatabase || !pool) return demoDashboard();
  const latest = await pool.query<{ id: string }>(
    `SELECT id FROM backtest_runs WHERE status='success' ORDER BY finished_at DESC LIMIT 1`,
  );
  if (!latest.rowCount) return demoDashboard();

  const runId = Number(latest.rows[0].id);
  const result = await pool.query<{
    id: string; event_ticker: string; contract_ticker: string; station_code: string;
    city: string; trade_date: string; triggered_at: Date; trigger_observed_at: Date;
    reaction_at: Date | null; reaction_lag_seconds: number | null; running_high_f: string;
    entry_price: string | null; gross_profit_per_contract: string | null;
    executable_proxy: boolean; limitation: string | null; label: string;
    final_value_f: string | null; settlement_source_name: string | null;
    settlement_source_url: string | null; outcome: string | null;
  }>(
    `SELECT bs.id, bs.event_ticker, bs.contract_ticker, bs.station_code, s.city,
            e.trade_date::text, bs.triggered_at, bs.trigger_observed_at, bs.reaction_at,
            bs.reaction_lag_seconds, bs.running_high_f, bs.entry_price,
            bs.gross_profit_per_contract, bs.executable_proxy, bs.limitation,
            mc.label, e.final_value_f, e.settlement_source_name,
            e.settlement_source_url, bs.outcome
       FROM backtest_signals bs
       JOIN market_events e USING (event_ticker)
       JOIN market_contracts mc USING (contract_ticker)
       JOIN stations s USING (station_code)
      WHERE bs.run_id=$1
      ORDER BY bs.triggered_at DESC`,
    [runId],
  );

  const cases: CaseStudy[] = result.rows.map((row) => {
    const observedAt = row.trigger_observed_at.toISOString();
    const availableAt = row.triggered_at.toISOString();
    const repricedAt = row.reaction_at?.toISOString() ?? null;
    const entryCents = row.entry_price === null ? null : Number(row.entry_price) * 100;
    const grossProfitCents = row.gross_profit_per_contract === null
      ? null : Number(row.gross_profit_per_contract) * 100;
    return {
      id: `signal_${row.id}`,
      date: row.trade_date.slice(0, 10),
      city: row.city,
      station: row.station_code,
      edgeClass: "market_repricing",
      headline: `${row.label} contradicted by a ${Number(row.running_high_f).toFixed(1)}°F received observation`,
      evidenceQuality: row.executable_proxy ? "high" : "medium",
      observedAt,
      availableAt,
      repricedAt,
      observationToAvailabilitySeconds: secondsBetween(observedAt, availableAt),
      availabilityToRepriceSeconds: row.reaction_lag_seconds,
      totalLatencySeconds: repricedAt ? secondsBetween(observedAt, repricedAt) : null,
      contract: `${row.label} (NO)`,
      entryCents,
      exitOrSettlementCents: row.outcome === "win" ? 100 : row.outcome === "loss" ? 0 : null,
      grossProfitCents,
      finalHighF: row.final_value_f === null ? null : Number(row.final_value_f),
      note: row.executable_proxy
        ? "Signal used an actual source receipt timestamp and the first available minute-candle quote."
        : "Diagnostic only: at least one execution-evidence gate failed.",
      sources: [
        ...(row.settlement_source_url ? [{ label: row.settlement_source_name ?? "Settlement source", url: row.settlement_source_url }] : []),
        { label: "Kalshi event", url: `https://api.elections.kalshi.com/trade-api/v2/events/${row.event_ticker}?with_nested_markets=true` },
      ],
      executableProxy: row.executable_proxy,
      limitation: row.limitation ?? "Minute candles do not prove fills or displayed size.",
    };
  });

  const cityRows: CitySummary[] = STATIONS.map((station) => {
    const stationCases = cases.filter((item) => item.station === station.station);
    const resolved = stationCases.filter((item) => item.exitOrSettlementCents !== null);
    const wins = resolved.filter((item) => item.exitOrSettlementCents === 100);
    const profits = stationCases.flatMap((item) => item.grossProfitCents === null ? [] : [item.grossProfitCents]);
    return {
      city: station.city,
      station: station.station,
      cases: stationCases.length,
      medianLagSeconds: median(stationCases.flatMap((item) =>
        item.availabilityToRepriceSeconds === null ? [] : [item.availabilityToRepriceSeconds],
      )),
      winRate: resolved.length ? wins.length / resolved.length : null,
      avgGrossProfitCents: profits.length ? profits.reduce((sum, value) => sum + value, 0) / profits.length : null,
      coverage: stationCases.length ? "live" : "pending",
    };
  });

  const health = await pool.query<{
    source: string; last_success_at: Date | null; consecutive_failures: number;
  }>(
    `SELECT source, max(last_success_at) AS last_success_at,
            max(consecutive_failures)::int AS consecutive_failures
       FROM source_health GROUP BY source ORDER BY source`,
  );
  const reactionLags = cases.flatMap((item) =>
    item.availabilityToRepriceSeconds === null ? [] : [item.availabilityToRepriceSeconds],
  );

  return {
    mode: "database",
    generatedAt: new Date().toISOString(),
    headline: {
      verifiedCases: cases.filter((item) => item.evidenceQuality === "high").length,
      candidateCases: cases.filter((item) => item.evidenceQuality !== "high").length,
      citiesCovered: cityRows.filter((city) => city.coverage === "live").length,
      medianMarketReactionSeconds: median(reactionLags),
      grossProfitCents: cases.reduce((sum, item) => sum + (item.grossProfitCents ?? 0), 0),
    },
    cases,
    cities: cityRows,
    latencyBuckets: latencyBuckets(reactionLags),
    sourceHealth: health.rows.map((row) => ({
      name: row.source,
      purpose: row.source.startsWith("NWS") ? "Official product releases" : row.source === "KALSHI" ? "Contract prices" : "Timestamped observations",
      status: row.consecutive_failures > 0 ? "stale" : "healthy",
      lastSeen: row.last_success_at?.toISOString() ?? null,
    })),
  };
}
