"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "@/app/weather-dashboard/weather-dashboard.module.css";

type WeatherRow = {
  time: string;
  temp: number | null;
  rh: number | null;
  windSpeed: number | null;
  windDirection: number | null;
  altimeter: number | null;
  seaLevelPressure: number | null;
  high6: number | null;
  low6: number | null;
  high24: number | null;
  low24: number | null;
  raw: string | null;
  kind: "hf" | "official" | "other";
};

type Station = {
  stid: string;
  city: string;
  name: string;
  timezone: string | null;
  latest: WeatherRow | null;
  hf: WeatherRow[];
  official: WeatherRow[];
  sixHour: WeatherRow[];
  daily: WeatherRow[];
  hfAvailable: boolean;
};

type DashboardData = {
  updatedAt: string;
  stations: Station[];
};

function temp(value: number | null) {
  return value === null ? "—" : `${value.toFixed(1)}°F`;
}

function value(value: number | null, suffix = "") {
  return value === null ? "—" : `${value.toFixed(1)}${suffix}`;
}

function timeLabel(iso: string, timezone: string | null) {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat("en-US", {
      hour: "numeric",
      minute: "2-digit",
      timeZone: timezone ?? undefined,
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

function windLabel(row: WeatherRow) {
  if (row.windSpeed === null && row.windDirection === null) return "—";
  const dir = row.windDirection === null ? "" : `${Math.round(row.windDirection)}°`;
  const speed = row.windSpeed === null ? "" : `${row.windSpeed.toFixed(0)} kt`;
  return [dir, speed].filter(Boolean).join(" · ");
}

function TableShell({ children }: { children: React.ReactNode }) {
  return <div className={styles.tableScroll}>{children}</div>;
}

function StationCard({ station }: { station: Station }) {
  const latest6 = station.sixHour[0] ?? null;
  const latest24 = station.daily[0] ?? null;

  return (
    <article className={styles.card} id={station.stid}>
      <header className={styles.cardHeader}>
        <div>
          <div className={styles.eyebrow}>{station.stid}</div>
          <h2>{station.city}</h2>
          <p>{station.name}</p>
        </div>
        <div className={styles.sourceBadge}>{station.hfAvailable ? "5-min HF live" : "Hourly only"}</div>
      </header>

      <section className={styles.heroReadout}>
        <div>
          <span>Latest</span>
          <strong>{temp(station.latest?.temp ?? null)}</strong>
          <small>{station.latest ? timeLabel(station.latest.time, station.timezone) : "No report"}</small>
        </div>
        <div className={styles.miniStats}>
          <div><span>6h high</span><b>{temp(latest6?.high6 ?? null)}</b></div>
          <div><span>6h low</span><b>{temp(latest6?.low6 ?? null)}</b></div>
          <div><span>24h high</span><b>{temp(latest24?.high24 ?? null)}</b></div>
          <div><span>RH</span><b>{value(station.latest?.rh ?? null, "%")}</b></div>
        </div>
      </section>

      {station.latest?.raw && (
        <details className={styles.latestRaw}>
          <summary>Latest raw report</summary>
          <code>{station.latest.raw}</code>
        </details>
      )}

      <section className={styles.sectionBlock}>
        <div className={styles.sectionTitle}>
          <div>
            <span>High frequency</span>
            <h3>5-minute ASOS</h3>
          </div>
          <small>Whole °C feed → displayed °F</small>
        </div>
        {station.hf.length ? (
          <TableShell>
            <table>
              <thead><tr><th>Time</th><th>Temp</th><th>Wind</th><th>RH</th><th>Alt</th></tr></thead>
              <tbody>
                {station.hf.map((row) => (
                  <tr key={`${row.time}-${row.raw ?? "hf"}`}>
                    <td>{timeLabel(row.time, station.timezone)}</td>
                    <td className={styles.tempCell}>{temp(row.temp)}</td>
                    <td>{windLabel(row)}</td>
                    <td>{value(row.rh, "%")}</td>
                    <td>{value(row.altimeter)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableShell>
        ) : (
          <div className={styles.empty}>No 5-minute HF-ASOS rows are available for this station in Synoptic.</div>
        )}
      </section>

      <section className={styles.sectionBlock}>
        <div className={styles.sectionTitle}>
          <div>
            <span>Official stream</span>
            <h3>Hourly / SPECI reports</h3>
          </div>
          <small>Precise METAR temperature when reported</small>
        </div>
        <div className={styles.reportList}>
          {station.official.length ? station.official.map((row) => (
            <details className={styles.report} key={`${row.time}-${row.raw ?? "official"}`}>
              <summary>
                <time>{timeLabel(row.time, station.timezone)}</time>
                <b>{temp(row.temp)}</b>
                <span>{windLabel(row)}</span>
              </summary>
              <code>{row.raw ?? "No raw METAR text"}</code>
            </details>
          )) : <div className={styles.empty}>No official reports in the current window.</div>}
        </div>
      </section>

      <section className={styles.sectionBlock}>
        <div className={styles.sectionTitle}>
          <div>
            <span>Max/min fields</span>
            <h3>6-hour reports</h3>
          </div>
          <small>Official reported high / low</small>
        </div>
        {station.sixHour.length ? (
          <TableShell>
            <table>
              <thead><tr><th>Time</th><th>6h high</th><th>6h low</th><th>Temp</th></tr></thead>
              <tbody>
                {station.sixHour.map((row) => (
                  <tr key={`${row.time}-six`}>
                    <td>{timeLabel(row.time, station.timezone)}</td>
                    <td className={styles.highCell}>{temp(row.high6)}</td>
                    <td>{temp(row.low6)}</td>
                    <td>{temp(row.temp)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableShell>
        ) : <div className={styles.empty}>No 6-hour max/min field in the current lookback yet.</div>}
      </section>
    </article>
  );
}

export function WeatherDashboardClient() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [selected, setSelected] = useState("KSEA");
  const [all, setAll] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const response = await fetch("/api/weather-dashboard", { cache: "no-store" });
      const next = await response.json();
      if (!response.ok) throw new Error(next.error ?? "Weather request failed");
      setData(next);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load weather data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 30_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const visible = useMemo(() => {
    if (!data) return [];
    return all ? data.stations : data.stations.filter((station) => station.stid === selected);
  }, [all, data, selected]);

  return (
    <div className={styles.dashboard}>
      <header className={styles.topbar}>
        <div>
          <span>Mercury Edge</span>
          <h1>Weather Reports</h1>
        </div>
        <button onClick={load} disabled={loading}>{loading ? "Loading…" : "Refresh"}</button>
      </header>

      <nav className={styles.stationNav} aria-label="Weather stations">
        {data?.stations.map((station) => (
          <button
            key={station.stid}
            className={!all && selected === station.stid ? styles.activeTab : ""}
            onClick={() => { setSelected(station.stid); setAll(false); }}
          >
            <b>{station.city}</b><small>{station.stid}</small>
          </button>
        ))}
        <button className={all ? styles.activeTab : ""} onClick={() => setAll(true)}>
          <b>All</b><small>scroll</small>
        </button>
      </nav>

      <div className={styles.statusLine}>
        <span className={error ? styles.badDot : styles.goodDot} />
        {error ? error : data ? `Live · refreshed ${new Date(data.updatedAt).toLocaleTimeString()}` : "Connecting to Synoptic…"}
      </div>

      <div className={styles.notice}>
        HF-ASOS temperatures are transmitted in whole °C. The displayed Fahrenheit number is a conversion of that coarse value; use the hourly/6-hour fields for precise official °F confirmation.
      </div>

      <main className={styles.cards}>
        {visible.map((station) => <StationCard station={station} key={station.stid} />)}
        {!visible.length && !loading && !error && <div className={styles.empty}>No station data returned.</div>}
      </main>
    </div>
  );
}
