import { runMechanicalLatencyBacktest, DEFAULT_ENGINE_CONFIG } from "./engine";
import { estimateFee } from "./fees";
import { pool } from "../db";
import { isLocalDate } from "../time";
import type { ContractBand, ObservationPoint, QuotePoint } from "../types";

type EventRow = {
  event_ticker: string;
  station_code: string;
  trade_date: string;
  timezone: string;
};

export type BacktestRunOptions = {
  stations?: string[];
  jobId?: string;
  onProgress?: (progress: { current: number; total: number; signals: number }) => Promise<void>;
  shouldContinue?: () => Promise<boolean>;
};

export async function executeBacktest(
  from: string,
  to: string,
  options: BacktestRunOptions = {},
) {
  if (!pool) throw new Error("DATABASE_URL is required for backtesting");
  const startedAt = new Date().toISOString();
  const runResult = await pool.query<{ id: string }>(
    `INSERT INTO backtest_runs
      (name, model_version, started_at, as_of_start, as_of_end, config, status, job_id)
     VALUES ('Mechanical receipt-to-reprice', 'latency-v0.1.0', $1, $2::date,
             ($3::date + interval '1 day'), $4, 'running', $5)
     RETURNING id`,
    [startedAt, from, to, { ...DEFAULT_ENGINE_CONFIG, stations: options.stations ?? [] }, options.jobId ?? null],
  );
  const runId = Number(runResult.rows[0].id);
  const eventResult = await pool.query<EventRow>(
    `SELECT e.event_ticker, e.station_code, e.trade_date::text, s.timezone
      FROM market_events e JOIN stations s USING (station_code)
      WHERE e.trade_date BETWEEN $1::date AND $2::date
        AND ($3::text[] IS NULL OR e.station_code = ANY($3::text[]))
      ORDER BY e.trade_date, e.station_code`,
    [from, to, options.stations?.length ? options.stations : null],
  );

  let signalCount = 0;
  let executableCount = 0;
  let wins = 0;
  let resolved = 0;
  let gross = 0;
  let fees = 0;
  let processed = 0;

  try {
    await options.onProgress?.({ current: 0, total: eventResult.rowCount ?? 0, signals: 0 });
    for (const event of eventResult.rows) {
      if (options.shouldContinue && !(await options.shouldContinue())) {
        throw new Error("JOB_CONTROLLED_STOP");
      }
      const contractResult = await pool.query<{
        ticker: string; label: string; lower_bound_f: string | null; upper_bound_f: string | null; result: "yes" | "no" | "unknown";
      }>(
        `SELECT ticker, label, lower_bound_f, upper_bound_f, result
           FROM market_contracts WHERE event_ticker = $1`,
        [event.event_ticker],
      );
      const contracts: ContractBand[] = contractResult.rows.map((row) => ({
        ticker: row.ticker,
        label: row.label,
        lower: row.lower_bound_f === null ? null : Number(row.lower_bound_f),
        upper: row.upper_bound_f === null ? null : Number(row.upper_bound_f),
        result: row.result,
      }));
      const tickers = contracts.map((contract) => contract.ticker);
      if (!tickers.length) {
        processed += 1;
        await options.onProgress?.({
          current: processed,
          total: eventResult.rowCount ?? 0,
          signals: signalCount,
        });
        continue;
      }

      const observationResult = await pool.query<{
        station_code: string; source: string; report_type: ObservationPoint["reportType"];
        observed_at: Date; received_at: Date; receipt_quality: ObservationPoint["receiptQuality"];
        temperature_f: string; settlement_compatible: boolean; raw_text: string | null;
      }>(
        `SELECT station_code, source, report_type, observed_at, received_at, receipt_quality,
                temperature_f, settlement_compatible, raw_text
           FROM weather_observations
          WHERE station_code = $1
            AND observed_at >= $2::date - interval '1 day'
            AND observed_at < $2::date + interval '2 days'
            AND temperature_f IS NOT NULL
          ORDER BY received_at`,
        [event.station_code, event.trade_date],
      );
      const observations: ObservationPoint[] = observationResult.rows
        .map((row) => ({
          station: row.station_code,
          source: row.source,
          reportType: row.report_type,
          observedAt: row.observed_at.toISOString(),
          receivedAt: row.received_at.toISOString(),
          receiptQuality: row.receipt_quality,
          temperatureF: Number(row.temperature_f),
          settlementCompatible: row.settlement_compatible,
          rawText: row.raw_text ?? undefined,
        }))
        .filter((point) => isLocalDate(point.observedAt, event.trade_date, event.timezone));
      const quoteResult = await pool.query<{
        contract_ticker: string; captured_at: Date; yes_bid: string | null;
        yes_ask: string | null; last_price: string | null;
      }>(
        `SELECT contract_ticker, captured_at, yes_bid, yes_ask, last_price
           FROM market_quotes
          WHERE contract_ticker = ANY($1::text[])
          ORDER BY captured_at`,
        [tickers],
      );
      const quotes: QuotePoint[] = quoteResult.rows.map((row) => ({
        contractTicker: row.contract_ticker,
        capturedAt: row.captured_at.toISOString(),
        yesBid: row.yes_bid === null ? null : Number(row.yes_bid),
        yesAsk: row.yes_ask === null ? null : Number(row.yes_ask),
        lastPrice: row.last_price === null ? null : Number(row.last_price),
      }));

      const signals = runMechanicalLatencyBacktest(event.station_code, contracts, observations, quotes);
      for (const signal of signals) {
        const contract = contracts.find((item) => item.ticker === signal.contractTicker);
        const resolvedOutcome = contract?.result === "no" ? "win" : contract?.result === "yes" ? "loss" : null;
        const grossProfit = signal.entryPrice === null || !resolvedOutcome
          ? null
          : resolvedOutcome === "win" ? 1 - signal.entryPrice : -signal.entryPrice;
        const fee = signal.entryPrice === null ? null : estimateFee(signal.entryPrice);
        await pool.query(
          `INSERT INTO backtest_signals
            (run_id, event_ticker, contract_ticker, station_code, edge_class,
             triggered_at, trigger_observed_at, running_high_f, official_floor_f,
             action, entry_price, quote_captured_at, reaction_at, reaction_lag_seconds,
             outcome, gross_profit_per_contract, fee_per_contract, net_profit_per_contract,
             executable_proxy, limitation, evidence)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21)`,
          [runId, event.event_ticker, signal.contractTicker, signal.station,
           signal.edgeClass, signal.triggeredAt, signal.triggerObservedAt,
           signal.runningHighF, signal.officialFloorF, signal.action, signal.entryPrice,
           signal.quoteCapturedAt, signal.reactionAt, signal.reactionLagSeconds,
           resolvedOutcome, grossProfit, fee, grossProfit === null || fee === null ? null : grossProfit - fee,
           signal.executableProxy, signal.limitation,
           { receiptGate: signal.executableProxy ? "passed" : "failed", model: "mechanical_only" }],
        );
        signalCount += 1;
        if (signal.executableProxy) executableCount += 1;
        if (resolvedOutcome) {
          resolved += 1;
          if (resolvedOutcome === "win") wins += 1;
        }
        if (grossProfit !== null) gross += grossProfit;
        if (fee !== null) fees += fee;
      }
      processed += 1;
      await options.onProgress?.({
        current: processed,
        total: eventResult.rowCount ?? 0,
        signals: signalCount,
      });
    }
    const summary = {
      events: eventResult.rowCount,
      signals: signalCount,
      executableSignals: executableCount,
      resolved,
      wins,
      winRate: resolved ? wins / resolved : null,
      grossProfitPerOneContract: gross,
      estimatedFees: fees,
      netProfitPerOneContract: gross - fees,
      warning: "Minute-candle backtests are quote proxies, not proof of fills or available size.",
    };
    await pool.query(
      `UPDATE backtest_runs SET finished_at=now(), status='success', summary=$2 WHERE id=$1`,
      [runId, summary],
    );
    return { runId, ...summary };
  } catch (error) {
    await pool.query(
      `UPDATE backtest_runs SET finished_at=now(), status='failed', summary=$2 WHERE id=$1`,
      [runId, { error: error instanceof Error ? error.message : String(error) }],
    );
    throw error;
  }
}
