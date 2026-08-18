"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "./LiveMonitor.module.css";

type LiveStation = {
  station: string;
  city: string;
  timezone: string;
  observedAt: string | null;
  receivedAt: string | null;
  temperatureF: number | null;
  runningHighF: number | null;
  officialFloorF: number | null;
  eventTicker: string | null;
  latestQuoteAt: string | null;
};

type LiveSignal = {
  station: string;
  city: string;
  timezone: string;
  eventTicker: string;
  contractTicker: string;
  label: string;
  upperBoundF: number;
  triggerObservedAt: string;
  triggeredAt: string;
  receiptQuality: string;
  triggerTemperatureF: number;
  runningHighF: number;
  officialFloorF: number;
  entryCapturedAt: string | null;
  entryYesBidCents: number | null;
  entryYesAskCents: number | null;
  noAskProxyCents: number | null;
  reactionAt: string | null;
  reactionLagSeconds: number | null;
  latestQuoteAt: string | null;
  latestYesBidCents: number | null;
  latestYesAskCents: number | null;
  latestLastPriceCents: number | null;
  executableProxy: boolean;
  status: "open_gap" | "repriced" | "awaiting_quote";
};

type LivePayload = {
  mode: "database" | "verified_demo";
  generatedAt: string;
  stations: LiveStation[];
  signals: LiveSignal[];
};

function fmtTemp(value: number | null) {
  return value === null ? "—" : `${value.toFixed(1)}°F`;
}

function fmtCents(value: number | null) {
  return value === null ? "—" : `${Math.round(value)}¢`;
}

function fmtLag(value: number | null) {
  if (value === null) return "—";
  if (value < 60) return `${value}s`;
  return `${Math.floor(value / 60)}m ${value % 60}s`;
}

function fmtClock(value: string | null, timezone?: string) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-US", {
    timeZone: timezone,
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
    timeZoneName: "short",
  }).format(new Date(value));
}

function ageLabel(value: string | null, now: number) {
  if (!value) return "No data yet";
  const seconds = Math.max(0, Math.round((now - new Date(value).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m ago`;
}

export function LiveMonitor() {
  const [data, setData] = useState<LivePayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const refresh = useCallback(async () => {
    try {
      const response = await fetch("/api/live", { cache: "no-store" });
      if (!response.ok) throw new Error(`Live endpoint returned ${response.status}`);
      setData(await response.json());
      setError(null);
      setNow(Date.now());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Live monitor unavailable");
    }
  }, []);

  useEffect(() => {
    // Defer the first async refresh to a timer callback so the effect itself does
    // not synchronously drive state updates under React's effect lint rules.
    const initial = window.setTimeout(() => void refresh(), 0);
    const timer = window.setInterval(() => void refresh(), 5_000);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
    };
  }, [refresh]);

  const activeStations = useMemo(
    () => data?.stations.filter((station) => station.receivedAt || station.latestQuoteAt || station.eventTicker) ?? [],
    [data],
  );
  const openGaps = data?.signals.filter((signal) => signal.status === "open_gap") ?? [];
  const repriced = data?.signals.filter((signal) => signal.status === "repriced") ?? [];

  return (
    <section className={styles.section} id="live-monitor">
      <a className={styles.floatingLink} href="#live-monitor">
        <i className={openGaps.length ? styles.hotDot : styles.liveDot} />
        {openGaps.length ? `${openGaps.length} live gap${openGaps.length === 1 ? "" : "s"}` : "Live monitor"}
      </a>

      <div className={styles.shell}>
        <header className={styles.header}>
          <div>
            <span className={styles.kicker}>Live monitor</span>
            <h2>New data, as it arrives.</h2>
            <p>
              Read-only view of today&apos;s stored weather receipts and market candles. It uses the same mechanical invalidation rule as the historical engine, but does not alter or launch backtests.
            </p>
          </div>
          <div className={styles.heartbeat}>
            <i className={error ? styles.errorDot : styles.liveDot} />
            <div>
              <strong>{error ? "Refresh issue" : "Polling every 5 seconds"}</strong>
              <span>{data ? `Last response ${ageLabel(data.generatedAt, now)}` : "Connecting…"}</span>
            </div>
          </div>
        </header>

        {error && <div className={styles.errorBanner}>{error}. Existing research controls and historical results are unaffected.</div>}

        <div className={styles.stationGrid}>
          {activeStations.map((station) => (
            <article className={styles.stationCard} key={station.station}>
              <div className={styles.stationTop}>
                <div><strong>{station.station}</strong><span>{station.city}</span></div>
                <small>{ageLabel(station.receivedAt ?? station.latestQuoteAt, now)}</small>
              </div>
              <div className={styles.stationMetrics}>
                <div><span>Latest</span><strong>{fmtTemp(station.temperatureF)}</strong></div>
                <div><span>Running high</span><strong>{fmtTemp(station.runningHighF)}</strong></div>
                <div><span>Official floor</span><strong>{station.officialFloorF === null ? "—" : `${station.officialFloorF}°F`}</strong></div>
              </div>
              <div className={styles.stationTimes}>
                <span>Weather receipt <b>{fmtClock(station.receivedAt, station.timezone)}</b></span>
                <span>Latest market candle <b>{fmtClock(station.latestQuoteAt, station.timezone)}</b></span>
              </div>
            </article>
          ))}
          {!activeStations.length && (
            <div className={styles.emptyState}>
              <strong>No live station records yet</strong>
              <span>The panel will populate automatically when the live-ingestion worker writes today&apos;s first observations or market candles.</span>
            </div>
          )}
        </div>

        <div className={styles.signalPanel}>
          <div className={styles.signalHeader}>
            <div>
              <span>Today&apos;s mechanical detections</span>
              <h3>New signals</h3>
            </div>
            <div className={styles.signalCounts}>
              <span><b>{openGaps.length}</b> open</span>
              <span><b>{repriced.length}</b> repriced</span>
              <span><b>{data?.signals.length ?? 0}</b> total</span>
            </div>
          </div>

          <div className={styles.signalList}>
            {data?.signals.map((signal) => (
              <article className={styles.signalRow} key={`${signal.station}-${signal.contractTicker}`}>
                <div className={styles.signalIdentity}>
                  <span className={`${styles.status} ${styles[signal.status]}`}>{signal.status.replace("_", " ")}</span>
                  <strong>{signal.station} · {signal.label}</strong>
                  <small>{signal.contractTicker}</small>
                </div>
                <div className={styles.signalMetric}><span>Triggered</span><strong>{fmtClock(signal.triggeredAt, signal.timezone)}</strong><small>{fmtTemp(signal.triggerTemperatureF)} receipt</small></div>
                <div className={styles.signalMetric}><span>Running high</span><strong>{fmtTemp(signal.runningHighF)}</strong><small>floor {signal.officialFloorF}°F</small></div>
                <div className={styles.signalMetric}><span>Latest YES ask</span><strong>{fmtCents(signal.latestYesAskCents)}</strong><small>{fmtClock(signal.latestQuoteAt, signal.timezone)}</small></div>
                <div className={styles.signalMetric}><span>NO entry proxy</span><strong>{fmtCents(signal.noAskProxyCents)}</strong><small>{signal.executableProxy ? "receipt + quote gate passed" : "diagnostic only"}</small></div>
                <div className={styles.signalMetric}><span>Reaction lag</span><strong>{fmtLag(signal.reactionLagSeconds)}</strong><small>{signal.reactionAt ? fmtClock(signal.reactionAt, signal.timezone) : "not repriced ≤5¢ yet"}</small></div>
              </article>
            ))}
            {data && !data.signals.length && (
              <div className={styles.emptySignals}>
                No settlement-compatible observation has invalidated a lower temperature contract today.
              </div>
            )}
            {!data && !error && <div className={styles.emptySignals}>Loading live database state…</div>}
          </div>
        </div>

        <p className={styles.caveat}>
          “Open” means the latest stored YES ask remains above 5¢ after a settlement-compatible observation made the bracket mechanically impossible. Minute candles remain quote proxies, not proof of available size or a fill.
        </p>
      </div>
    </section>
  );
}
