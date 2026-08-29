"use client";

import { useEffect, useMemo, useState } from "react";
import styles from "@/components/LaxCapWatch.module.css";

type WeatherRow = {
  time: string;
  temp: number | null;
  dewPoint: number | null;
  windSpeed: number | null;
  windDirection: number | null;
  raw: string | null;
};

type Station = {
  stid: string;
  city: string;
  timezone: string | null;
  official: WeatherRow[];
  hf?: WeatherRow[];
};

type ForecastPoint = {
  time: string;
  temp: number;
};

type ForecastBaseline = {
  localDate: string;
  points: ForecastPoint[];
};

type DashboardPayload = { stations: Station[] };
type NwsPayload = { forecasts: Array<{ stid: string; baseline: ForecastBaseline }> };
type SignalPoint = { time: string; minute: number; value: number };
type Standout = { time: string; title: string; detail: string; raw: string | null };

const LAX = "KLAX";
const MORNING_START = 8 * 60 + 30;
const MORNING_END = 12 * 60 + 10;

function localDate(iso: string, timezone: string) {
  const parts = new Intl.DateTimeFormat("en-US", {
    year: "numeric", month: "2-digit", day: "2-digit", timeZone: timezone,
  }).formatToParts(new Date(iso));
  const year = parts.find((part) => part.type === "year")?.value;
  const month = parts.find((part) => part.type === "month")?.value;
  const day = parts.find((part) => part.type === "day")?.value;
  return year && month && day ? `${year}-${month}-${day}` : "";
}

function minuteOfDay(iso: string, timezone: string) {
  const parts = new Intl.DateTimeFormat("en-US", {
    hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: timezone,
  }).formatToParts(new Date(iso));
  const hour = Number(parts.find((part) => part.type === "hour")?.value ?? "0");
  const minute = Number(parts.find((part) => part.type === "minute")?.value ?? "0");
  return hour * 60 + minute;
}

function timeLabel(iso: string, timezone: string) {
  return new Intl.DateTimeFormat("en-US", {
    hour: "numeric", minute: "2-digit", timeZone: timezone,
  }).format(new Date(iso));
}

// Meteorological direction is where the wind comes FROM.
// Positive = easterly/offshore support at LAX; negative = westerly/onshore marine flow.
function offshoreComponent(speed: number | null, direction: number | null) {
  if (speed === null || direction === null) return null;
  const radians = (direction - 90) * Math.PI / 180;
  return speed * Math.cos(radians);
}

function mean(values: number[]) {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
}

function nearestAtOrBefore(rows: WeatherRow[], targetMs: number, maxAgeMinutes = 80) {
  let best: WeatherRow | null = null;
  for (const row of rows) {
    const ms = new Date(row.time).getTime();
    if (ms > targetMs) continue;
    if (!best || ms > new Date(best.time).getTime()) best = row;
  }
  if (!best) return null;
  return targetMs - new Date(best.time).getTime() <= maxAgeMinutes * 60_000 ? best : null;
}

function interpolateForecast(points: ForecastPoint[], minute: number, timezone: string) {
  const series = points
    .map((point) => ({ minute: minuteOfDay(point.time, timezone), temp: point.temp }))
    .sort((a, b) => a.minute - b.minute);
  for (let index = 0; index < series.length - 1; index += 1) {
    const left = series[index];
    const right = series[index + 1];
    if (minute >= left.minute && minute <= right.minute) {
      const width = right.minute - left.minute;
      if (width <= 0) return left.temp;
      return left.temp + ((minute - left.minute) / width) * (right.temp - left.temp);
    }
  }
  const nearest = [...series].sort((a, b) => Math.abs(a.minute - minute) - Math.abs(b.minute - minute))[0];
  return nearest && Math.abs(nearest.minute - minute) <= 75 ? nearest.temp : null;
}

function rollingOffshore(rows: WeatherRow[], endMs: number, minutes: number) {
  const startMs = endMs - minutes * 60_000;
  const values = rows
    .filter((row) => {
      const ms = new Date(row.time).getTime();
      return ms >= startMs && ms <= endMs;
    })
    .map((row) => offshoreComponent(row.windSpeed, row.windDirection))
    .filter((value): value is number => value !== null);
  return mean(values);
}

function signed(value: number | null, digits = 1, suffix = " mph") {
  if (value === null) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}${suffix}`;
}

function buildModel(station: Station, baseline: ForecastBaseline | null) {
  const timezone = station.timezone;
  if (!timezone) return null;
  const targetDate = baseline?.localDate ?? localDate(new Date().toISOString(), timezone);
  const combined = [...(station.hf ?? []), ...station.official]
    .filter((row) => localDate(row.time, timezone) === targetDate)
    .sort((a, b) => new Date(a.time).getTime() - new Date(b.time).getTime());

  const byTime = new Map<string, WeatherRow>();
  for (const row of combined) byTime.set(row.time, row);
  const rows = [...byTime.values()].sort((a, b) => new Date(a.time).getTime() - new Date(b.time).getTime());

  const offshore: SignalPoint[] = [];
  const persistence: SignalPoint[] = [];
  for (const row of rows) {
    const component = offshoreComponent(row.windSpeed, row.windDirection);
    if (component === null) continue;
    const minute = minuteOfDay(row.time, timezone);
    offshore.push({ time: row.time, minute, value: component });
    const rolling = rollingOffshore(rows, new Date(row.time).getTime(), 60);
    if (rolling !== null) persistence.push({ time: row.time, minute, value: rolling });
  }

  const latestWindRow = [...rows].reverse().find((row) => offshoreComponent(row.windSpeed, row.windDirection) !== null) ?? null;
  const latestOffshore = latestWindRow ? offshoreComponent(latestWindRow.windSpeed, latestWindRow.windDirection) : null;
  const latestPersistence = latestWindRow ? rollingOffshore(rows, new Date(latestWindRow.time).getTime(), 60) : null;
  const latestDew = [...rows].reverse().find((row) => row.dewPoint !== null) ?? null;
  const dewPrior = latestDew ? nearestAtOrBefore(rows.filter((row) => row.dewPoint !== null), new Date(latestDew.time).getTime() - 60 * 60_000) : null;
  const dewChange = latestDew?.dewPoint !== null && latestDew?.dewPoint !== undefined && dewPrior?.dewPoint !== null && dewPrior?.dewPoint !== undefined
    ? latestDew.dewPoint - dewPrior.dewPoint
    : null;

  let runway: "OPEN" | "MIXED" | "VULNERABLE" | "CAPPED" = "MIXED";
  if ((latestOffshore ?? 0) <= -4 || (latestPersistence ?? 0) <= -2) runway = "CAPPED";
  else if ((latestOffshore ?? 0) >= 6 && (latestPersistence ?? 0) >= 6) runway = "OPEN";
  else if ((latestOffshore ?? 0) <= 5 && (latestPersistence ?? 0) <= 6) runway = "VULNERABLE";

  const standouts: Standout[] = [];
  const officialToday = station.official
    .filter((row) => localDate(row.time, timezone) === targetDate)
    .sort((a, b) => new Date(a.time).getTime() - new Date(b.time).getTime());

  for (const row of officialToday) {
    const minute = minuteOfDay(row.time, timezone);
    if (minute < MORNING_START || minute > MORNING_END || row.temp === null) continue;
    const reportMs = new Date(row.time).getTime();
    const currentOffshore = offshoreComponent(row.windSpeed, row.windDirection);
    if (currentOffshore === null) continue;
    const hold60 = rollingOffshore(rows, reportMs, 60);
    const holdPrior30 = rollingOffshore(rows, reportMs - 5 * 60_000, 30);
    const priorTemp = nearestAtOrBefore(rows.filter((item) => item.temp !== null), reportMs - 60 * 60_000);
    const tempGain60 = priorTemp?.temp !== null && priorTemp?.temp !== undefined ? row.temp - priorTemp.temp : null;
    const forecastTemp = baseline ? interpolateForecast(baseline.points, minute, timezone) : null;
    const tempResidual = forecastTemp !== null ? row.temp - forecastTemp : null;
    const priorDew = nearestAtOrBefore(rows.filter((item) => item.dewPoint !== null), reportMs - 60 * 60_000);
    const dewChange60 = row.dewPoint !== null && priorDew?.dewPoint !== null && priorDew?.dewPoint !== undefined ? row.dewPoint - priorDew.dewPoint : null;

    const marineTurn = currentOffshore <= -5 && (holdPrior30 === null || holdPrior30 > -2);
    if (marineTurn) {
      standouts.push({
        time: row.time,
        title: "MARINE CAP ARRIVAL",
        detail: `${timeLabel(row.time, timezone)} METAR: onshore component ${signed(currentOffshore)}${dewChange60 === null ? "" : ` · dew point ${dewChange60 >= 0 ? "+" : ""}${dewChange60.toFixed(1)}°F/60m`}. The wind regime has turned marine; remaining heating runway is being cut now.`,
        raw: row.raw,
      });
      continue;
    }

    const weakHold = currentOffshore <= 5 && (hold60 === null || hold60 <= 6);
    const hotOrRising = (tempGain60 !== null && tempGain60 >= 1.5) || (tempResidual !== null && tempResidual >= 1);
    if (weakHold && hotOrRising) {
      const heatEvidence = [
        tempGain60 === null ? null : `temp ${tempGain60 >= 0 ? "+" : ""}${tempGain60.toFixed(1)}°F/60m`,
        tempResidual === null ? null : `${tempResidual >= 0 ? "+" : ""}${tempResidual.toFixed(1)}°F vs NWS path`,
      ].filter(Boolean).join(" · ");
      standouts.push({
        time: row.time,
        title: "UPPER-TAIL CAP WATCH",
        detail: `${timeLabel(row.time, timezone)} METAR: ${heatEvidence || "temperature remains hot"}, but offshore hold is only ${signed(currentOffshore)}${hold60 === null ? "" : ` · 60m hold ${signed(hold60)}`}. Hot reading without strong offshore support: high-temperature buckets are vulnerable to reassessment.`,
        raw: row.raw,
      });
    }
  }

  return { targetDate, offshore, persistence, latestOffshore, latestPersistence, dewChange, runway, standouts };
}

function SignalStrip({ title, subtitle, points }: { title: string; subtitle: string; points: SignalPoint[] }) {
  const xMin = 6 * 60;
  const xMax = 15 * 60;
  const visible = points.filter((point) => point.minute >= xMin && point.minute <= xMax);
  const width = 760;
  const height = 112;
  const pad = { left: 42, right: 16, top: 10, bottom: 28 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const maxAbs = Math.max(6, ...visible.map((point) => Math.abs(point.value)));
  const x = (minute: number) => pad.left + ((minute - xMin) / (xMax - xMin)) * plotW;
  const y = (value: number) => pad.top + ((maxAbs - value) / (2 * maxAbs)) * plotH;
  const line = visible.map((point) => `${x(point.minute)},${y(point.value)}`).join(" ");
  const ticks = [6, 8, 9, 10, 11, 12, 13, 15];
  const latest = visible[visible.length - 1] ?? null;
  return (
    <div className={styles.signalRow}>
      <div className={styles.signalHeader}>
        <div><b>{title}</b><small>{subtitle}</small></div>
        <strong>{latest ? signed(latest.value) : "—"}</strong>
      </div>
      <div className={styles.chartScroll}>
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title}>
          <line x1={pad.left} x2={width - pad.right} y1={y(0)} y2={y(0)} className={styles.zeroLine} />
          {ticks.map((hour) => <line key={hour} x1={x(hour * 60)} x2={x(hour * 60)} y1={pad.top} y2={height - pad.bottom} className={styles.gridLine} />)}
          {line && <polyline points={line} className={styles.signalLine} />}
          {visible.map((point) => <circle key={point.time} cx={x(point.minute)} cy={y(point.value)} r="2.3" className={styles.signalDot} />)}
          {ticks.map((hour) => <text key={`t-${hour}`} x={x(hour * 60)} y={height - 8} textAnchor="middle" className={styles.axis}>{hour > 12 ? hour - 12 : hour}{hour >= 12 ? "p" : "a"}</text>)}
        </svg>
      </div>
    </div>
  );
}

export function LaxCapWatch() {
  const [station, setStation] = useState<Station | null>(null);
  const [baseline, setBaseline] = useState<ForecastBaseline | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [weatherResponse, nwsResponse] = await Promise.all([
          fetch(`/api/weather-dashboard?laxCap=${Date.now()}`, { cache: "no-store" }),
          fetch(`/api/weather-dashboard/nws-forecast?laxCap=${Date.now()}`, { cache: "no-store" }),
        ]);
        const weather = await weatherResponse.json() as DashboardPayload & { error?: string };
        const nws = await nwsResponse.json() as NwsPayload & { error?: string };
        if (!weatherResponse.ok) throw new Error(weather.error ?? "Weather feed failed");
        const lax = weather.stations.find((item) => item.stid === LAX) ?? null;
        const nwsBaseline = nwsResponse.ok ? nws.forecasts.find((item) => item.stid === LAX)?.baseline ?? null : null;
        if (!cancelled) {
          setStation(lax);
          setBaseline(nwsBaseline);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Unable to load LAX cap watch");
      }
    };
    void load();
    const timer = window.setInterval(load, 30_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  const model = useMemo(() => station ? buildModel(station, baseline) : null, [station, baseline]);
  if (error) return <section className={styles.watch}><div className={styles.empty}>{error}</div></section>;
  if (!station || !model) return null;

  return (
    <section className={styles.watch}>
      <header className={styles.header}>
        <div><span>LAX cap watch · {model.targetDate}</span><h2>Offshore hold → marine takeover → heating cap</h2></div>
        <small>Positive = easterly/offshore support. Negative = westerly/onshore marine flow. This is a transparent diagnostic, not a fitted probability model.</small>
      </header>

      <div className={styles.stateGrid}>
        <div><span>Offshore hold now</span><b>{signed(model.latestOffshore)}</b></div>
        <div><span>60m persistence</span><b>{signed(model.latestPersistence)}</b></div>
        <div><span>Dew point Δ 60m</span><b>{model.dewChange === null ? "—" : `${model.dewChange > 0 ? "+" : ""}${model.dewChange.toFixed(1)}°F`}</b></div>
        <div className={styles.runway}><span>Remaining heating runway</span><b>{model.runway}</b><small>provisional regime label</small></div>
      </div>

      <SignalStrip title="Offshore hold" subtitle="Up = easterly flow defending heating · down = marine/onshore flow" points={model.offshore} />
      <SignalStrip title="60-minute offshore persistence" subtitle="Sustained positive values matter more than one weak easterly print" points={model.persistence} />

      {model.standouts.length > 0 && (
        <div className={styles.alertStack}>
          {model.standouts.map((alert) => (
            <article className={styles.alert} key={`${alert.time}-${alert.title}`}>
              <div className={styles.alertTitle}><span>STANDOUT METAR</span><strong>{alert.title}</strong></div>
              <p>{alert.detail}</p>
              {alert.raw && <code>{alert.raw}</code>}
            </article>
          ))}
        </div>
      )}

      <p className={styles.note}>The hourly red notice only appears when the report carries a concrete LAX cap signal: either temperature is still hot/rising while offshore support is weak, or the wind has actually flipped into a marine regime. The 5-minute lines stay visible continuously so the lead-up can be learned visually.</p>
    </section>
  );
}
