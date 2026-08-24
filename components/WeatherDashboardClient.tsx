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

type TrajectoryPoint = {
  minute: number;
  temp: number;
  kind: "observed" | "forecast";
};

const SEA_ORIGINAL = [
  { minute: 11 * 60, temp: 66 },
  { minute: 12 * 60, temp: 69 },
  { minute: 13 * 60, temp: 71 },
  { minute: 14 * 60, temp: 75 },
  { minute: 15 * 60, temp: 76 },
  { minute: 16 * 60, temp: 76 },
  { minute: 17 * 60, temp: 78 },
  { minute: 18 * 60, temp: 76 },
  { minute: 19 * 60, temp: 75 },
  { minute: 20 * 60, temp: 72 },
  { minute: 21 * 60, temp: 68 },
  { minute: 22 * 60, temp: 67 },
  { minute: 23 * 60, temp: 66 },
];

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

function minuteOfDay(iso: string, timezone: string | null) {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
      timeZone: timezone ?? undefined,
    }).formatToParts(new Date(iso));
    const hour = Number(parts.find((p) => p.type === "hour")?.value ?? "0");
    const minute = Number(parts.find((p) => p.type === "minute")?.value ?? "0");
    return hour * 60 + minute;
  } catch {
    return null;
  }
}

function windLabel(row: WeatherRow) {
  if (row.windSpeed === null && row.windDirection === null) return "—";
  const dir = row.windDirection === null ? "" : `${Math.round(row.windDirection)}°`;
  const speed = row.windSpeed === null ? "" : `${row.windSpeed.toFixed(0)} kt`;
  return [dir, speed].filter(Boolean).join(" · ");
}

function pollDelayMs() {
  const minute = new Date().getMinutes();
  if (minute >= 49 || minute <= 2) return 2_000;
  if (minute >= 47) return 5_000;
  return 20_000;
}

function seaBaselineAt(minute: number) {
  for (let i = 0; i < SEA_ORIGINAL.length - 1; i += 1) {
    const left = SEA_ORIGINAL[i];
    const right = SEA_ORIGINAL[i + 1];
    if (minute >= left.minute && minute <= right.minute) {
      const w = (minute - left.minute) / (right.minute - left.minute);
      return left.temp + (right.temp - left.temp) * w;
    }
  }
  return null;
}

function buildSeattleTrajectory(station: Station) {
  const anchors = station.official
    .filter((row) => row.temp !== null)
    .map((row) => ({ row, minute: minuteOfDay(row.time, station.timezone) }))
    .filter((item): item is { row: WeatherRow; minute: number } => item.minute !== null)
    .filter((item) => item.minute % 60 >= 50)
    .filter((item) => item.minute >= SEA_ORIGINAL[0].minute && item.minute <= SEA_ORIGINAL[SEA_ORIGINAL.length - 1].minute)
    .sort((a, b) => a.minute - b.minute);

  const residuals = anchors
    .map((item) => {
      const baseline = seaBaselineAt(item.minute);
      if (baseline === null || item.row.temp === null) return null;
      return { minute: item.minute, temp: item.row.temp, residual: item.row.temp - baseline };
    })
    .filter((item): item is { minute: number; temp: number; residual: number } => item !== null);

  if (!residuals.length) return null;

  const latest = residuals[residuals.length - 1];
  const slopes: number[] = [];
  for (let i = 1; i < residuals.length; i += 1) {
    const hours = (residuals[i].minute - residuals[i - 1].minute) / 60;
    if (hours > 0) slopes.push((residuals[i].residual - residuals[i - 1].residual) / hours);
  }

  const recent = slopes.slice(-3).reverse();
  const weights = [0.5, 0.3, 0.2];
  let momentum = recent.reduce((sum, slope, index) => sum + slope * weights[index], 0);
  momentum *= 0.5;
  momentum = Math.max(-1.5, Math.min(1.5, momentum));

  const levelHalfLifeHours = 3;
  const momentumHalfLifeHours = 1;
  const levelLambda = Math.log(2) / levelHalfLifeHours;
  const momentumLambda = Math.log(2) / momentumHalfLifeHours;

  const points: TrajectoryPoint[] = residuals.map((item) => ({
    minute: item.minute,
    temp: item.temp,
    kind: "observed",
  }));

  for (let minute = latest.minute + 60; minute <= 20 * 60 + 53; minute += 60) {
    const baseline = seaBaselineAt(minute);
    if (baseline === null) continue;
    const dtHours = (minute - latest.minute) / 60;
    const residual =
      latest.residual * Math.exp(-levelLambda * dtHours) +
      momentum * dtHours * Math.exp(-momentumLambda * dtHours);
    points.push({ minute, temp: baseline + residual, kind: "forecast" });
  }

  const future = points.filter((point) => point.kind === "forecast");
  const peak = future.length ? future.reduce((best, point) => (point.temp > best.temp ? point : best), future[0]) : null;

  return {
    points,
    latestResidual: latest.residual,
    momentum,
    peak,
  };
}

function clockLabel(minute: number) {
  const h24 = Math.floor(minute / 60) % 24;
  const m = minute % 60;
  const suffix = h24 >= 12 ? "PM" : "AM";
  const h = h24 % 12 || 12;
  return `${h}:${String(m).padStart(2, "0")} ${suffix}`;
}

function SeattleTrajectory({ station }: { station: Station }) {
  const model = useMemo(() => buildSeattleTrajectory(station), [station]);
  if (!model) return null;

  const baseline = SEA_ORIGINAL.filter((point) => point.minute >= 11 * 60 && point.minute <= 21 * 60);
  const allTemps = [...baseline.map((p) => p.temp), ...model.points.map((p) => p.temp)];
  const yMin = Math.floor(Math.min(...allTemps) - 1);
  const yMax = Math.ceil(Math.max(...allTemps) + 1);
  const xMin = 11 * 60;
  const xMax = 21 * 60;
  const width = 700;
  const height = 280;
  const pad = { left: 42, right: 16, top: 18, bottom: 34 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const x = (minute: number) => pad.left + ((minute - xMin) / (xMax - xMin)) * plotW;
  const y = (t: number) => pad.top + ((yMax - t) / (yMax - yMin)) * plotH;
  const line = (pts: { minute: number; temp: number }[]) => pts.map((p) => `${x(p.minute)},${y(p.temp)}`).join(" ");
  const ticks = [11, 13, 15, 17, 19, 21];
  const observed = model.points.filter((p) => p.kind === "observed");
  const projected = model.points.filter((p) => p.kind === "forecast");
  const adaptive = projected.length && observed.length ? [observed[observed.length - 1], ...projected] : projected;

  return (
    <section className={styles.trajectoryBlock}>
      <div className={styles.sectionTitle}>
        <div>
          <span>Adaptive trajectory</span>
          <h3>Original NWS vs hourly-adjusted</h3>
        </div>
        <small>Seattle · original NWS curve issued 11:26 AM PDT</small>
      </div>

      <div className={styles.trajectoryStats}>
        <div><span>Latest deviation</span><b>{model.latestResidual >= 0 ? "+" : ""}{model.latestResidual.toFixed(1)}°F</b></div>
        <div><span>Residual momentum</span><b>{model.momentum >= 0 ? "+" : ""}{model.momentum.toFixed(1)}°F/hr</b></div>
        <div><span>Adaptive peak</span><b>{model.peak ? `${model.peak.temp.toFixed(1)}°F` : "—"}</b></div>
        <div><span>Peak time</span><b>{model.peak ? clockLabel(model.peak.minute) : "—"}</b></div>
      </div>

      <div className={styles.trajectoryLegend}>
        <span><i className={styles.legendOriginal} />Original NWS</span>
        <span><i className={styles.legendObserved} />Precise hourly</span>
        <span><i className={styles.legendAdaptive} />Adaptive future</span>
      </div>

      <div className={styles.chartScroll}>
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Seattle original NWS temperature curve, precise hourly observations, and adaptive future trajectory">
          {[yMin, Math.round((yMin + yMax) / 2), yMax].map((tick) => (
            <g key={tick}>
              <line x1={pad.left} x2={width - pad.right} y1={y(tick)} y2={y(tick)} className={styles.gridLine} />
              <text x={pad.left - 8} y={y(tick) + 4} textAnchor="end" className={styles.axisText}>{tick}°</text>
            </g>
          ))}
          {ticks.map((hour) => (
            <text key={hour} x={x(hour * 60)} y={height - 10} textAnchor="middle" className={styles.axisText}>{hour > 12 ? hour - 12 : hour}{hour >= 12 ? "p" : "a"}</text>
          ))}
          <polyline points={line(baseline)} className={styles.originalLine} />
          {observed.length > 1 && <polyline points={line(observed)} className={styles.observedLine} />}
          {adaptive.length > 1 && <polyline points={line(adaptive)} className={styles.adaptiveLine} />}
          {observed.map((point) => <circle key={`o-${point.minute}`} cx={x(point.minute)} cy={y(point.temp)} r="4" className={styles.observedDot} />)}
          {projected.map((point) => <circle key={`f-${point.minute}`} cx={x(point.minute)} cy={y(point.temp)} r="3.5" className={styles.adaptiveDot} />)}
        </svg>
      </div>

      <p className={styles.trajectoryNote}>
        Prototype logic: each precise hourly report re-estimates the deviation from the original curve. The deviation can expand or shrink; future deviation mean-reverts gradually while recent residual momentum decays faster. This allows a day to diverge, re-couple, then diverge again instead of forcing a fixed parallel shift.
      </p>
    </section>
  );
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

      {station.stid === "KSEA" && <SeattleTrajectory station={station} />}

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
      const response = await fetch(`/api/weather-dashboard?ts=${Date.now()}`, {
        cache: "no-store",
        headers: { "Cache-Control": "no-cache" },
      });
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
    let cancelled = false;
    let timer: number | undefined;

    const tick = async () => {
      await load();
      if (!cancelled) timer = window.setTimeout(tick, pollDelayMs());
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [load]);

  const visible = useMemo(() => {
    if (!data) return [];
    return all ? data.stations : data.stations.filter((station) => station.stid === selected);
  }, [all, data, selected]);

  const rapid = (() => {
    const minute = new Date().getMinutes();
    return minute >= 49 || minute <= 2;
  })();

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
        {error ? error : data ? `${rapid ? "Rapid 2s official-report polling" : "Live"} · refreshed ${new Date(data.updatedAt).toLocaleTimeString()}` : "Connecting to Synoptic…"}
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
