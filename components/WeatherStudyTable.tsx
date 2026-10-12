"use client";

import { useEffect, useMemo, useState } from "react";
import styles from "@/components/WeatherStudyTable.module.css";

type StudyValue = {
  date: string;
  forecastHigh: number | null;
  laterForecastHigh: number | null;
  realizedHigh: number | null;
  revision: number | null;
  error: number | null;
  laterError: number | null;
  hit: boolean | null;
  laterHit: boolean | null;
  forecastCapturedAt: string | null;
  laterForecastCapturedAt: string | null;
  forecastProvenance: string | null;
  eventTicker: string | null;
  winningBucket: {
    ticker: string;
    label: string;
    lower: number | null;
    upper: number | null;
  } | null;
  settled: boolean;
};

type StudyRow = {
  stid: string;
  city: string;
  values: StudyValue[];
  mae: number | null;
  laterMae: number | null;
  hits: number;
  hitTotal: number;
  laterHits: number;
  laterHitTotal: number;
};

type StudyPayload = {
  dates: string[];
  rows: StudyRow[];
  updatedAt: string;
  definition: string;
  error?: string;
};

function shortDate(date: string) {
  const parsed = new Date(`${date}T12:00:00.000Z`);
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(parsed);
}

function degree(value: number | null) {
  if (value === null) return "—";
  return Number.isInteger(value) ? `${value.toFixed(0)}°` : `${value.toFixed(1)}°`;
}

function errorText(value: number | null) {
  if (value === null) return "";
  const rounded = Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1);
  return value > 0 ? `+${rounded}` : rounded;
}

function cellText(value: StudyValue | undefined) {
  if (!value) return "—";
  const revision = value.revision === null ? "—" : `${errorText(value.revision)}°`;
  const realized = value.settled && value.realizedHigh !== null
    ? degree(value.realizedHigh) : "pending";
  const errors = value.error === null && value.laterError === null
    ? null
    : `Frozen ${value.error === null ? "—" : `${errorText(value.error)}°`} · Later ${value.laterError === null ? "—" : `${errorText(value.laterError)}°`}`;

  return (
    <div className={styles.forecastCell}>
      <div className={styles.forecastMain}>
        <span className={styles.cellLabel}>Frozen</span> <b>{degree(value.forecastHigh)}</b>
        <span className={styles.cellArrow}> → </span>
        <span className={styles.cellLabel}>Later</span> <b>{degree(value.laterForecastHigh)}</b>
      </div>
      <div className={styles.forecastSub}>Realized <b>{realized}</b> · Revision {revision}</div>
      {errors && <div className={styles.forecastSub}>Error: {errors}</div>}
    </div>
  );
}

function capturedTitle(value: StudyValue | undefined) {
  if (!value) return undefined;
  const parts = [
    value.forecastCapturedAt ? `Frozen TWC captured ${new Date(value.forecastCapturedAt).toLocaleString()}` : null,
    value.forecastProvenance,
    value.laterForecastCapturedAt ? `Later TWC captured ${new Date(value.laterForecastCapturedAt).toLocaleString()}` : "No same-day TWC update recorded",
    value.winningBucket ? `Winning bucket: ${value.winningBucket.label}` : null,
    value.eventTicker,
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : undefined;
}

export function WeatherStudyTable() {
  const [data, setData] = useState<StudyPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const response = await fetch("/api/weather-dashboard/study", { cache: "no-store" });
        const payload = await response.json() as StudyPayload;
        if (!response.ok) throw new Error(payload.error ?? "Unable to load study");
        if (!cancelled) {
          setData(payload);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Unable to load study");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void load();
    const timer = window.setInterval(() => void load(), 10 * 60 * 1000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const dateMap = useMemo(() => {
    return new Map((data?.dates ?? []).map((date, index) => [date, index]));
  }, [data?.dates]);

  return (
    <section className={styles.study}>
      <header className={styles.header}>
        <div>
          <span>Calibration study</span>
          <h2>Frozen vs later TWC high · realized result</h2>
          <p>
            {data?.dates?.length
              ? `${shortDate(data.dates[0])}–${shortDate(data.dates[data.dates.length - 1])} · ${data.dates.length} completed days; frozen pre-day forecast → latest archived same-day forecast → realized high. Hover for snapshot times.`
              : "Completed days · frozen pre-day TWC forecast → latest archived same-day TWC forecast → realized high."}
          </p>
        </div>
        <div className={styles.legend}><b>MAE</b><span>Mean absolute error</span></div>
      </header>

      {loading && !data && <p className={styles.note}>Loading the latest settled study window…</p>}
      {error && !data && <p className={styles.note}>Study unavailable: {error}</p>}

      {data && (
        <>
          <div className={styles.tableWrap}>
            <table>
              <thead>
                <tr>
                  <th>City</th>
                  {data.dates.map((date) => <th key={date}>{shortDate(date)}</th>)}
                  <th>Mean absolute error</th>
                  <th>Winning-bucket hits</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row) => (
                  <tr key={row.stid}>
                    <td><b>{row.city}</b></td>
                    {data.dates.map((date) => {
                      const value = row.values[dateMap.get(date) ?? -1];
                      return <td key={date} title={capturedTitle(value)}>{cellText(value)}</td>;
                    })}
                    <td className={styles.mae}>
                      <div className={styles.metricLine}><span>Frozen</span> {row.mae === null ? "—" : `${row.mae.toFixed(2)}°F`}</div>
                      <div className={styles.metricLine}><span>Later</span> {row.laterMae === null ? "—" : `${row.laterMae.toFixed(2)}°F`}</div>
                    </td>
                    <td>
                      <div className={styles.metricLine}><span>Frozen</span> <span className={styles.hit}>{row.hitTotal ? `${row.hits}/${row.hitTotal}` : "—"}</span></div>
                      <div className={styles.metricLine}><span>Later</span> <span className={styles.hit}>{row.laterHitTotal ? `${row.laterHits}/${row.laterHitTotal}` : "—"}</span></div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className={styles.note}>
            Frozen = latest valid pre-midnight forecast; Later = last archived TWC calendar-day-high forecast on that city’s local target day (timestamp on hover); Realized = settlement high. Revision = Later − Frozen. Both forecast errors = forecast − realized. Bucket hits and MAE are shown separately for frozen and later forecasts, with potentially different sample sizes when a snapshot is missing. Later is retrospective, not a pre-day trading signal. Missing forecasts stay blank; dates continue to append after the Pacific day closes.
          </p>
        </>
      )}
    </section>
  );
}
