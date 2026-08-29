"use client";

import { useEffect, useMemo, useState } from "react";
import styles from "@/components/FullDayHfArchive.module.css";

type HfRow = {
  time: string;
  temp: number | null;
  dewPoint: number | null;
  rh: number | null;
  windSpeed: number | null;
  windDirection: number | null;
  altimeter: number | null;
  raw: string | null;
};

type Station = {
  stid: string;
  city: string;
  timezone: string;
  localDate: string;
  count: number;
  rows: HfRow[];
};

type Payload = { updatedAt: string; stations: Station[]; error?: string };

function timeLabel(iso: string, timezone: string) {
  return new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone: timezone,
  }).format(new Date(iso));
}

function temp(value: number | null) {
  return value === null ? "—" : `${value.toFixed(1)}°F`;
}

function value(value: number | null, suffix = "") {
  return value === null ? "—" : `${value.toFixed(1)}${suffix}`;
}

function wind(row: HfRow) {
  if (row.windDirection === null && row.windSpeed === null) return "—";
  const direction = row.windDirection === null ? "" : `${Math.round(row.windDirection)}°`;
  const speed = row.windSpeed === null ? "" : `${row.windSpeed.toFixed(0)} mph`;
  return [direction, speed].filter(Boolean).join(" · ");
}

export function FullDayHfArchive() {
  const [data, setData] = useState<Payload | null>(null);
  const [selected, setSelected] = useState("KLAX");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const response = await fetch(`/api/weather-dashboard/hf-day?v=${Date.now()}`, { cache: "no-store" });
        const payload = await response.json() as Payload;
        if (!response.ok) throw new Error(payload.error ?? "Full-day HF feed failed");
        if (!cancelled) {
          setData(payload);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Unable to load full-day HF history");
      }
    };
    void load();
    const timer = window.setInterval(load, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const station = useMemo(
    () => data?.stations.find((item) => item.stid === selected) ?? data?.stations[0] ?? null,
    [data, selected],
  );

  if (error) return <section className={styles.archive}><div className={styles.empty}>{error}</div></section>;
  if (!station) return <section className={styles.archive}><div className={styles.empty}>Loading full-day HF-ASOS history…</div></section>;

  return (
    <section className={styles.archive}>
      <header className={styles.header}>
        <div>
          <span>Persistent intraday tape</span>
          <h2>Full-day 5-minute HF-ASOS</h2>
        </div>
        <small>{station.localDate} · {station.count} observations · midnight onward</small>
      </header>

      <nav className={styles.tabs} aria-label="Full-day HF station">
        {data?.stations.map((item) => (
          <button
            key={item.stid}
            className={item.stid === station.stid ? styles.active : ""}
            onClick={() => setSelected(item.stid)}
          >
            {item.city} <em>{item.count}</em>
          </button>
        ))}
      </nav>

      <p className={styles.note}>Rows do not roll off during the local calendar day. New HF observations are added at the top; the page simply grows downward so the entire intraday sequence remains visually reviewable.</p>

      {station.rows.length ? (
        <div className={styles.tableScroll}>
          <table>
            <thead>
              <tr><th>Time</th><th>Temp</th><th>Dew</th><th>Wind</th><th>RH</th><th>Alt</th><th>Raw HF report</th></tr>
            </thead>
            <tbody>
              {station.rows.map((row) => (
                <tr key={`${row.time}-${row.raw ?? "hf"}`}>
                  <td>{timeLabel(row.time, station.timezone)}</td>
                  <td className={styles.temp}>{temp(row.temp)}</td>
                  <td>{temp(row.dewPoint)}</td>
                  <td>{wind(row)}</td>
                  <td>{value(row.rh, "%")}</td>
                  <td>{value(row.altimeter)}</td>
                  <td><code>{row.raw ?? "—"}</code></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <div className={styles.empty}>No HF-ASOS rows have been reported for this local day yet.</div>}
    </section>
  );
}
