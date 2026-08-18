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
    executable: number;
    resolved: number;
    wins: number;
    winRate: number | null;
    averageLagSeconds: number | null;
    grossProfit: number | null;
    netProfit: number | null;
  }>;
  signals: Array<{
    id: number;
    station: string;
    date: string;
    contract: string;
    triggeredAt: string;
    entryCents: number | null;
    reactionLagSeconds: number | null;
    executable: boolean;
    outcome: string | null;
    netProfit: number | null;
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
  }>;
};

type Tab = "overview" | "stations" | "signals" | "coverage" | "audit";

const TABS: Array<{ id: Tab; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "stations", label: "Stations" },
  { id: "signals", label: "Signals" },
  { id: "coverage", label: "Coverage" },
  { id: "audit", label: "Audit" },
];

function number(value: unknown, digits = 0) {
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? numeric.toLocaleString(undefined, { maximumFractionDigits: digits })
    : "—";
}

function percent(value: unknown) {
  if (value === null || value === undefined) return "—";
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${number(numeric * 100, 1)}%` : "—";
}

function seconds(value: number | null) {
  if (value === null) return "—";
  if (value < 60) return `${Math.round(value)}s`;
  return `${Math.floor(value / 60)}m ${Math.round(value % 60)}s`;
}

function dollars(value: unknown) {
  if (value === null || value === undefined) return "—";
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `$${number(numeric, 2)}` : "—";
}

function cents(value: number | null) {
  return value === null ? "—" : `${number(value, 0)}¢`;
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
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(value));
}

function resultLabel(outcome: string | null) {
  if (!outcome) return "Open";
  return outcome.charAt(0).toUpperCase() + outcome.slice(1);
}

export function BacktestReport({ results }: { results: ResultsPayload }) {
  const [tab, setTab] = useState<Tab>("overview");
  const summary = results.latestRun?.summary ?? {};
  const topStations = useMemo(
    () => [...results.stations].sort((a, b) => (b.netProfit ?? -Infinity) - (a.netProfit ?? -Infinity)).slice(0, 4),
    [results.stations],
  );
  const recentSignals = results.signals.slice(0, 6);

  return (
    <section className={styles.report} id="backtest-report">
      <div className={styles.heading}>
        <div>
          <span>Latest completed model run</span>
          <h3>Backtest report</h3>
        </div>
        <small>
          {results.latestRun
            ? `${results.latestRun.modelVersion} · ${dateTime(results.latestRun.finishedAt)}`
            : "Waiting for first completed run"}
        </small>
      </div>

      <div className={styles.metrics}>
        <div><span>Events</span><strong>{number(summary.events)}</strong></div>
        <div><span>Signals</span><strong>{number(summary.signals)}</strong></div>
        <div><span>Executable</span><strong>{number(summary.executableSignals)}</strong></div>
        <div><span>Win rate</span><strong>{percent(summary.winRate)}</strong></div>
        <div className={styles.netMetric}><span>Net / one-contract</span><strong>{dollars(summary.netProfitPerOneContract)}</strong></div>
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
              <div className={styles.blockTitle}><span>Station snapshot</span><small>sorted by net</small></div>
              {topStations.length ? topStations.map((row) => (
                <div className={styles.snapshotRow} key={row.station}>
                  <strong>{row.station}</strong>
                  <span>{row.signals} signals</span>
                  <span>{percent(row.winRate)} wins</span>
                  <b>{dollars(row.netProfit)}</b>
                </div>
              )) : <p className={styles.empty}>No station results yet.</p>}
            </div>
            <div className={styles.summaryBlock}>
              <div className={styles.blockTitle}><span>Newest signals</span><small>latest first</small></div>
              {recentSignals.length ? recentSignals.map((signal) => (
                <div className={styles.snapshotRow} key={signal.id}>
                  <strong>{signal.station}</strong>
                  <span>{signal.contract}</span>
                  <span>{seconds(signal.reactionLagSeconds)}</span>
                  <b className={signal.outcome === "loss" ? styles.negative : ""}>{resultLabel(signal.outcome)}</b>
                </div>
              )) : <p className={styles.empty}>No signals in the latest run.</p>}
            </div>
          </div>
        )}

        {tab === "stations" && (
          <>
            <div className={styles.tableWrap}>
              <div className={`${styles.table} ${styles.stationTable}`}>
                <div className={styles.tableHead}><span>Station</span><span>Signals</span><span>Executable</span><span>Win rate</span><span>Avg lag</span><span>Net</span></div>
                {results.stations.map((row) => (
                  <div className={styles.tableRow} key={row.station}>
                    <span><b>{row.station}</b></span><span>{row.signals}</span><span>{row.executable}</span><span>{percent(row.winRate)}</span><span>{seconds(row.averageLagSeconds)}</span><span>{dollars(row.netProfit)}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className={styles.mobileCards}>
              {results.stations.map((row) => (
                <article key={row.station}>
                  <header><strong>{row.station}</strong><b>{dollars(row.netProfit)}</b></header>
                  <div><span>Signals <b>{row.signals}</b></span><span>Executable <b>{row.executable}</b></span><span>Win rate <b>{percent(row.winRate)}</b></span><span>Avg lag <b>{seconds(row.averageLagSeconds)}</b></span></div>
                </article>
              ))}
            </div>
          </>
        )}

        {tab === "signals" && (
          <>
            <div className={styles.tableWrap}>
              <div className={`${styles.table} ${styles.signalTable}`}>
                <div className={styles.tableHead}><span>Station</span><span>Date</span><span>Contract</span><span>Trigger</span><span>Entry</span><span>Lag</span><span>Result</span><span>Net</span></div>
                {results.signals.map((signal) => (
                  <div className={styles.tableRow} key={signal.id}>
                    <span><b>{signal.station}</b></span><span>{dateOnly(signal.date)}</span><span title={signal.contract}>{signal.contract}</span><span>{dateTime(signal.triggeredAt)}</span><span>{cents(signal.entryCents)}</span><span>{seconds(signal.reactionLagSeconds)}</span><span><i className={signal.executable ? styles.pass : styles.diagnostic}>{resultLabel(signal.outcome)}</i></span><span>{dollars(signal.netProfit)}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className={styles.mobileCards}>
              {results.signals.map((signal) => (
                <article key={signal.id}>
                  <header><strong>{signal.station} · {signal.contract}</strong><i className={signal.executable ? styles.pass : styles.diagnostic}>{resultLabel(signal.outcome)}</i></header>
                  <div><span>Date <b>{dateOnly(signal.date)}</b></span><span>Entry <b>{cents(signal.entryCents)}</b></span><span>Lag <b>{seconds(signal.reactionLagSeconds)}</b></span><span>Net <b>{dollars(signal.netProfit)}</b></span></div>
                  <small>Triggered {dateTime(signal.triggeredAt)} · {signal.executable ? "execution gate passed" : "diagnostic only"}</small>
                </article>
              ))}
            </div>
            {!results.signals.length && <p className={styles.empty}>No signals in the latest completed run.</p>}
          </>
        )}

        {tab === "coverage" && (
          <>
            <div className={styles.tableWrap}>
              <div className={`${styles.table} ${styles.coverageTable}`}>
                <div className={styles.tableHead}><span>Station</span><span>Market days</span><span>Observations</span><span>Actual receipts</span><span>Archive-only</span><span>Quotes</span></div>
                {results.coverage.map((row) => (
                  <div className={styles.tableRow} key={row.station}>
                    <span><b>{row.station}</b></span><span>{row.marketDays}</span><span>{number(row.observations)}</span><span>{number(row.actualReceipts)}</span><span>{number(row.discoveryOnly)}</span><span>{number(row.quotes)}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className={styles.mobileCards}>
              {results.coverage.map((row) => (
                <article key={row.station}>
                  <header><strong>{row.station}</strong><b>{row.marketDays} market days</b></header>
                  <div><span>Observations <b>{number(row.observations)}</b></span><span>Actual receipts <b>{number(row.actualReceipts)}</b></span><span>Archive-only <b>{number(row.discoveryOnly)}</b></span><span>Quotes <b>{number(row.quotes)}</b></span></div>
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
              <p>Backtest entries remain minute-candle quote proxies, not guaranteed fills. Receipt quality, execution gates, fees, and non-executable evidence remain preserved by the underlying run.</p>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
