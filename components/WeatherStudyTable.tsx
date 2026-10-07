"use client";

import { useEffect, useMemo, useState } from "react";
import styles from "@/components/WeatherStudyTable.module.css";

type StudyValue = {
  date: string;
  forecastHigh: number | null;
  realizedHigh: number | null;
  error: number | null;
  hit: boolean | null;
  forecastCapturedAt: string | null;
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
  hits: number;
  hitTotal: number;
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
  if (value.forecastHigh === null) return "No frozen TWC";
  if (!value.settled || value.realizedHigh === null) return `${degree(value.forecastHigh)} → pending`;
  return `${degree(value.forecastHigh)} → ${degree(value.realizedHigh)} (${errorText(value.error)})`;
}

function capturedTitle(value: StudyValue | undefined) {
  if (!value) return undefined;
  const parts = [
    value.forecastCapturedAt ? `Frozen TWC captured ${new Date(value.forecastCapturedAt).toLocaleString()}` : null,
    value.forecastProvenance,
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
          <h2>TWC forecast high vs realized daily high</h2>
          <p>
            {data?.dates?.length
              ? `${shortDate(data.dates[0])}–${shortDate(data.dates[data.dates.length - 1])} · all ${data.dates.length} completed recorded market days; frozen forecast on the left, realized settlement high on the right, forecast error in parentheses.`
              : "All completed recorded market days · frozen forecast on the left, realized settlement high on the right, forecast error in parentheses."}
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
                  <th>Recorded-day MAE</th>
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
                    <td className={styles.mae}>{row.mae === null ? "—" : `${row.mae.toFixed(2)}°F`}</td>
                    <td><span className={styles.hit}>{row.hitTotal ? `${row.hits}/${row.hitTotal}` : "—"}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className={styles.note}>
            Error sign = TWC frozen calendar-day high − realized Kalshi/TWC settlement high. “Winning-bucket hit” means the frozen TWC high itself falls inside the bucket that settled YES. The study starts at Mercury’s first recorded TWC baseline and keeps every completed day permanently. New dates append automatically after the Pacific calendar day closes; incomplete or unavailable cells are shown explicitly instead of being filled from a later forecast.
          </p>
        </>
      )}
    </section>
  );
}
