"use client";

import { useEffect, useMemo, useState } from "react";
import styles from "@/components/WeatherStudyTable.module.css";

type StudyValue = {
  date: string;
  forecastHigh: number | null;
  laterForecastHigh: number | null;
  realizedHigh: number | null;
  error: number | null;
  laterError: number | null;
  hit: boolean | null;
  laterHit: boolean | null;
  forecastCapturedAt: string | null;
  forecastProvenance: string | null;
  laterForecastCapturedAt: string | null;
  laterForecastProvenance: string | null;
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
  maeCount: number;
  laterMae: number | null;
  laterMaeCount: number;
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

function forecastError(value: number | null) {
  return value === null ? "" : ` (error ${errorText(value)}°)`;
}

function cellContent(value: StudyValue | undefined) {
  if (!value) return "—";
  return (
    <div className={styles.forecastCell}>
      <div className={styles.forecastLine}><span>Frozen TWC</span><b>{degree(value.forecastHigh)}{forecastError(value.error)}</b></div>
      <div className={styles.forecastLine}><span>Later TWC</span><b>{degree(value.laterForecastHigh)}{forecastError(value.laterError)}</b></div>
      <div className={styles.actualLine}><span>Settled high</span><b>{value.settled ? degree(value.realizedHigh) : "Pending"}</b></div>
    </div>
  );
}

function capturedTitle(value: StudyValue | undefined) {
  if (!value) return undefined;
  const parts = [
    value.forecastCapturedAt ? `Frozen TWC captured ${new Date(value.forecastCapturedAt).toLocaleString()}` : null,
    value.forecastProvenance,
    value.laterForecastCapturedAt ? `Later TWC captured ${new Date(value.laterForecastCapturedAt).toLocaleString()}` : "No later same-day TWC high archived",
    value.laterForecastProvenance,
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
          <h2>Frozen vs later TWC high · settlement</h2>
          <p>
            {data?.dates?.length
              ? `${shortDate(data.dates[0])}–${shortDate(data.dates[data.dates.length - 1])} · ${data.dates.length} completed market days · original frozen forecast, one last archived same-day forecast, and actual settlement high.`
              : "Completed market days · original frozen forecast, one last archived same-day forecast, and actual settlement high."}
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
                  <th>Forecast MAE</th>
                  <th>Winning-bucket hits</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row) => (
                  <tr key={row.stid}>
                    <td><b>{row.city}</b></td>
                    {data.dates.map((date) => {
                      const value = row.values[dateMap.get(date) ?? -1];
                      return <td key={date} title={capturedTitle(value)}>{cellContent(value)}</td>;
                    })}
                    <td className={styles.mae}>
                      <div className={styles.metricLine}><span>Frozen</span><b>{row.mae === null ? "—" : `${row.mae.toFixed(2)}°F`}</b><small>n={row.maeCount}</small></div>
                      <div className={styles.metricLine}><span>Later</span><b>{row.laterMae === null ? "—" : `${row.laterMae.toFixed(2)}°F`}</b><small>n={row.laterMaeCount}</small></div>
                    </td>
                    <td>
                      <div className={styles.metricLine}><span>Frozen</span><span className={styles.hit}>{row.hitTotal ? `${row.hits}/${row.hitTotal}` : "—"}</span></div>
                      <div className={styles.metricLine}><span>Later</span><span className={styles.hit}>{row.laterHitTotal ? `${row.laterHits}/${row.laterHitTotal}` : "—"}</span></div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className={styles.note}>
            Error sign = forecast high − realized Kalshi/TWC settlement high, for each TWC version separately. The later TWC value is the last archived same-day daily-high forecast (not a settlement reading), with its capture time shown when you tap/hover a cell. Each MAE uses available settled days; the n counts show coverage if the two samples differ. A winning-bucket hit means that forecast falls inside the bucket that settled YES. Missing snapshots remain blank rather than being invented from later days. New days append automatically after the Pacific day closes.
          </p>
        </>
      )}
    </section>
  );
}
