"use client";

import { useMemo, useState } from "react";
import styles from "./BacktestReport.module.css";

export type ResultsPayload = {
  generatedAt?: string;
  latestRun: null | {
    id: number;
    modelVersion: string;
    startedAt?: string;
    finishedAt: string | null;
    from?: string;
    to?: string;
    status?: string;
    summary: Record<string, unknown>;
  };
  stations: Array<{
    station: string;
    signals: number;
    violent: number;
    hardState: number;
    candidateProxy: number;
    tapeObserved: number;
    l2Simulated: number;
    proxyResolved: number;
    proxyWins: number;
    proxyWinRate: number | null;
    averageLagSeconds: number | null;
    proxyNetProfit: number | null;
    tapeCounterfactualNetProfit: number | null;
  }>;
  signals: Array<{
    id: number;
    station: string;
    date: string;
    contract: string;
    triggeredAt: string;
    entryCents: number | null;
    reactionLagSeconds: number | null;
    reactionLagLowerSeconds: number | null;
    reactionLagUpperSeconds: number | null;
    triggerSource: string | null;
    triggerReportType: string | null;
    triggerKind: string | null;
    triggerReceiptQuality: string | null;
    triggerRawText: string | null;
    hardStateProven: boolean;
    candidateProxy: boolean;
    evidenceTier: string;
    violent: boolean;
    violentMoveCents: number | null;
    staleEdgeCents: number | null;
    observedNoTakerQuantity: number;
    observedNoTakerVwap: number | null;
    tapeCounterfactualNetProfit: number | null;
    profitStatus: string;
    executable: boolean;
    outcome: string | null;
    proxyNetProfit: number | null;
    limitation: string | null;
  }>;
  coverage: Array<{
    station: string;
    firstObservation: string | null;
    lastObservation: string | null;
    observations: number;
    actualReceipts: number;
    discoveryOnly: number;
    marketDays: number;
    quotes: number;
    trades: number;
  }>;
};

type Tab = "overview" | "stations" | "signals" | "coverage" | "audit";
type SignalFilter = "all" | "violent" | "hard_state" | "trade_tape";

const TABS: Array<{ id: Tab; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "stations", label: "Stations" },
  { id: "signals", label: "Episodes" },
  { id: "coverage", label: "Coverage" },
  { id: "audit", label: "Audit" },
];

const SIGNAL_FILTERS: Array<{ id: SignalFilter; label: string }> = [
  { id: "all", label: "All eliminations" },
  { id: "violent", label: "Violent only" },
  { id: "hard_state", label: "Proven trigger" },
  { id: "trade_tape", label: "Tape observed" },
];

function number(value: unknown, digits = 0) {
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? numeric.toLocaleString(undefined, { maximumFractionDigits: digits })
    : "—";
}

function seconds(value: number | null) {
  if (value === null) return "—";
  if (value < 60) return `${Math.round(value)}s`;
  return `${Math.floor(value / 60)}m ${Math.round(value % 60)}s`;
}

function latencyRange(lower: number | null, upper: number | null) {
  if (lower === null || upper === null) return "—";
  return Math.round(lower) === Math.round(upper)
    ? seconds(upper)
    : `${seconds(lower)}–${seconds(upper)}`;
}

function dollars(value: unknown) {
  if (value === null || value === undefined) return "—";
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `$${number(numeric, 2)}` : "—";
}

function cents(value: number | null, digits = 0) {
  return value === null ? "—" : `${number(value, digits)}¢`;
}

function dateTime(value?: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function dateOnly(value?: string | null) {
  if (!value) return "—";
  const normalized = /^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00Z` : value;
  return new Intl.DateTimeFormat("en-US", {
    timeZone: "UTC",
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(normalized));
}

function reportLabel(signal: ResultsPayload["signals"][number]) {
  const report = signal.triggerReportType ?? "Report";
  const kind = signal.triggerKind?.replaceAll("_", " ");
  return kind ? `${report} · ${kind}` : report;
}

function evidenceLabel(tier: string) {
  if (tier === "l2_simulated") return "L2 replay";
  if (tier === "trade_tape_observed") return "Public tape";
  if (tier === "minute_candle_proxy") return "Minute proxy";
  return "Weather only";
}

function fillLabel(signal: ResultsPayload["signals"][number]) {
  if (signal.evidenceTier === "trade_tape_observed") {
    const price = signal.observedNoTakerVwap === null
      ? "unknown price"
      : cents(signal.observedNoTakerVwap * 100, 1);
    return `${number(signal.observedNoTakerQuantity, 2)} @ ${price} observed`;
  }
  if (signal.evidenceTier === "l2_simulated") return "L2 simulated";
  if (signal.evidenceTier === "minute_candle_proxy") return "No historical size";
  return "No market evidence";
}

function profitLabel(signal: ResultsPayload["signals"][number]) {
  if (signal.executable) return `${dollars(signal.proxyNetProfit)} L2 simulation`;
  if (signal.tapeCounterfactualNetProfit !== null) {
    return `${dollars(signal.tapeCounterfactualNetProfit)} tape counterfactual`;
  }
  if (signal.proxyNetProfit !== null) return `${dollars(signal.proxyNetProfit)} 1-contract proxy`;
  return "Not estimated";
}

export function BacktestReport({ results }: { results: ResultsPayload }) {
  const [tab, setTab] = useState<Tab>("overview");
  const [signalFilter, setSignalFilter] = useState<SignalFilter>("all");
  const summary = results.latestRun?.summary ?? {};
  const topStations = useMemo(
    () => [...results.stations]
      .sort((a, b) => b.violent - a.violent || (b.proxyNetProfit ?? -Infinity) - (a.proxyNetProfit ?? -Infinity))
      .slice(0, 4),
    [results.stations],
  );
  const filteredSignals = useMemo(() => results.signals.filter((signal) => {
    if (signalFilter === "violent") return signal.violent;
    if (signalFilter === "hard_state") return signal.hardStateProven;
    if (signalFilter === "trade_tape") return signal.evidenceTier === "trade_tape_observed";
    return true;
  }), [results.signals, signalFilter]);
  const recentSignals = results.signals.slice(0, 6);

  return (
    <section className={styles.report} id="backtest-report">
      <div className={styles.heading}>
        <div>
          <span>Evidence-tiered episode census</span>
          <h3>Elimination backtest</h3>
        </div>
        <small>
          {results.latestRun
            ? `${results.latestRun.modelVersion} · ${dateTime(results.latestRun.finishedAt)}`
            : "Waiting for first completed run"}
        </small>
      </div>

      <div className={styles.metrics}>
        <div><span>Events</span><strong>{number(summary.events)}</strong></div>
        <div><span>Eliminations</span><strong>{number(summary.signals)}</strong></div>
        <div><span>Violent</span><strong>{number(summary.violentSignals)}</strong></div>
        <div><span>Tape observed</span><strong>{number(summary.tradeTapeObservedSignals)}</strong></div>
        <div className={styles.netMetric}><span>L2-sim P&amp;L</span><strong>{dollars(summary.l2SimulatedNetProfit)}</strong></div>
      </div>

      <div className={styles.tabs} role="tablist" aria-label="Backtest report sections">
        {TABS.map((item) => (
          <button
            type="button"
            key={item.id}
            className={tab === item.id ? styles.activeTab : ""}
            onClick={() => setTab(item.id)}
            role="tab"
            aria-selected={tab === item.id}
          >
            {item.label}
            {item.id === "signals" && results.signals.length > 0 && <b>{results.signals.length}</b>}
          </button>
        ))}
      </div>

      <div className={styles.panel}>
        {tab === "overview" && (
          <div className={styles.overviewGrid}>
            <div className={styles.summaryBlock}>
              <div className={styles.blockTitle}><span>Station snapshot</span><small>violent first</small></div>
              {topStations.length ? topStations.map((row) => (
                <div className={styles.snapshotRow} key={row.station}>
                  <strong>{row.station}</strong>
                  <span>{row.violent} violent / {row.signals} total</span>
                  <span>{row.tapeObserved} tape</span>
                  <b>{dollars(row.proxyNetProfit)} proxy</b>
                </div>
              )) : <p className={styles.empty}>No station results yet.</p>}
            </div>
            <div className={styles.summaryBlock}>
              <div className={styles.blockTitle}><span>Largest collapses</span><small>post-event filter</small></div>
              {recentSignals.length ? recentSignals.map((signal) => (
                <div className={styles.snapshotRow} key={signal.id}>
                  <strong>{signal.station}</strong>
                  <span>{signal.contract}</span>
                  <span>{reportLabel(signal)}</span>
                  <b>{cents(signal.violentMoveCents)}</b>
                </div>
              )) : <p className={styles.empty}>No elimination episodes in the latest run.</p>}
            </div>
          </div>
        )}

        {tab === "stations" && (
          <>
            <div className={styles.tableWrap}>
              <div className={`${styles.table} ${styles.stationTable}`}>
                <div className={styles.tableHead}><span>Station</span><span>Episodes</span><span>Violent</span><span>Proven</span><span>Tape</span><span>Proxy P&amp;L</span></div>
                {results.stations.map((row) => (
                  <div className={styles.tableRow} key={row.station}>
                    <span><b>{row.station}</b></span><span>{row.signals}</span><span>{row.violent}</span><span>{row.hardState}</span><span>{row.tapeObserved}</span><span>{dollars(row.proxyNetProfit)}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className={styles.mobileCards}>
              {results.stations.map((row) => (
                <article key={row.station}>
                  <header><strong>{row.station}</strong><b>{dollars(row.proxyNetProfit)} proxy</b></header>
                  <div><span>Episodes <b>{row.signals}</b></span><span>Violent <b>{row.violent}</b></span><span>Proven <b>{row.hardState}</b></span><span>Tape <b>{row.tapeObserved}</b></span></div>
                </article>
              ))}
            </div>
          </>
        )}

        {tab === "signals" && (
          <>
            <div className={styles.filters} aria-label="Filter elimination episodes">
              {SIGNAL_FILTERS.map((filter) => (
                <button
                  type="button"
                  key={filter.id}
                  className={signalFilter === filter.id ? styles.activeFilter : ""}
                  onClick={() => setSignalFilter(filter.id)}
                >
                  {filter.label}
                </button>
              ))}
              <small>{filteredSignals.length} shown</small>
            </div>
            <div className={styles.tableWrap}>
              <div className={`${styles.table} ${styles.signalTable}`}>
                <div className={styles.tableHead}><span>Day</span><span>Contract</span><span>Trigger report</span><span>Collapse</span><span>Edge</span><span>Latency</span><span>Fill evidence</span><span>Profit status</span></div>
                {filteredSignals.map((signal) => (
                  <div className={styles.tableRow} key={signal.id} title={signal.limitation ?? undefined}>
                    <span><b>{signal.station}</b> · {dateOnly(signal.date)}</span>
                    <span title={signal.contract}>{signal.contract}</span>
                    <span title={[signal.triggerSource, signal.triggerReceiptQuality, signal.triggerRawText].filter(Boolean).join(" · ") || undefined}>{reportLabel(signal)}</span>
                    <span><i className={signal.violent ? styles.violent : styles.diagnostic}>{cents(signal.violentMoveCents)}</i></span>
                    <span>{cents(signal.staleEdgeCents)}</span>
                    <span>{latencyRange(signal.reactionLagLowerSeconds, signal.reactionLagUpperSeconds)}</span>
                    <span title={evidenceLabel(signal.evidenceTier)}><i className={signal.evidenceTier === "trade_tape_observed" ? styles.pass : styles.diagnostic}>{fillLabel(signal)}</i></span>
                    <span title={signal.limitation ?? undefined}>{profitLabel(signal)}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className={styles.mobileCards}>
              {filteredSignals.map((signal) => (
                <article key={signal.id}>
                  <header><strong>{signal.station} · {signal.contract}</strong><i className={signal.violent ? styles.violent : styles.diagnostic}>{signal.violent ? `${cents(signal.violentMoveCents)} collapse` : "Not violent"}</i></header>
                  <div><span>Date <b>{dateOnly(signal.date)}</b></span><span>Edge <b>{cents(signal.staleEdgeCents)}</b></span><span>Latency <b>{latencyRange(signal.reactionLagLowerSeconds, signal.reactionLagUpperSeconds)}</b></span><span>Fill evidence <b>{fillLabel(signal)}</b></span></div>
                  <small>{reportLabel(signal)} at {dateTime(signal.triggeredAt)} · {profitLabel(signal)}. {signal.limitation}</small>
                </article>
              ))}
            </div>
            {!filteredSignals.length && <p className={styles.empty}>No episodes match this evidence filter.</p>}
          </>
        )}

        {tab === "coverage" && (
          <>
            <div className={styles.tableWrap}>
              <div className={`${styles.table} ${styles.coverageTable}`}>
                <div className={styles.tableHead}><span>Station</span><span>Market days</span><span>Observations</span><span>Actual receipts</span><span>Archive-only</span><span>Candles</span><span>Trades</span></div>
                {results.coverage.map((row) => (
                  <div className={styles.tableRow} key={row.station}>
                    <span><b>{row.station}</b></span><span>{row.marketDays}</span><span>{number(row.observations)}</span><span>{number(row.actualReceipts)}</span><span>{number(row.discoveryOnly)}</span><span>{number(row.quotes)}</span><span>{number(row.trades)}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className={styles.mobileCards}>
              {results.coverage.map((row) => (
                <article key={row.station}>
                  <header><strong>{row.station}</strong><b>{row.marketDays} market days</b></header>
                  <div><span>Observations <b>{number(row.observations)}</b></span><span>Actual receipts <b>{number(row.actualReceipts)}</b></span><span>Candles <b>{number(row.quotes)}</b></span><span>Trades <b>{number(row.trades)}</b></span></div>
                </article>
              ))}
            </div>
          </>
        )}

        {tab === "audit" && (
          <div className={styles.auditGrid}>
            <div><span>Run ID</span><strong>{results.latestRun?.id ?? "—"}</strong></div>
            <div><span>Model</span><strong>{results.latestRun?.modelVersion ?? "—"}</strong></div>
            <div><span>Research window</span><strong>{results.latestRun?.from && results.latestRun?.to ? `${dateOnly(results.latestRun.from)} → ${dateOnly(results.latestRun.to)}` : "—"}</strong></div>
            <div><span>Completed</span><strong>{dateTime(results.latestRun?.finishedAt)}</strong></div>
            <div className={styles.auditNote}>
              <span>Interpretation</span>
              <p>“Violent” is a descriptive after-the-fact filter: a dead bucket had at least a 20¢ YES bid immediately before the trigger and lost at least 25¢ within 15 minutes. Minute candles only bound reaction time to a 60-second interval. Public tape proves another taker traded, not that Mercury would have won that liquidity. Candle and tape counterfactuals use the configured quadratic M=1 fee assumption. L2-simulated P&amp;L stays blank until a sequenced replay applies decision latency, displayed depth, queue assumptions, and a verified event-time fee schedule; it would still be simulated, not realized.</p>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
