"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import styles from "@/app/weather-dashboard/weather-dashboard.module.css";

type WeatherRow = {
  time: string;
  temp: number | null;
  dewPoint: number | null;
  cloudCover: number | null;
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
  reportType?: "METAR" | "SPECI" | "HF" | "OTHER";
};

type ForecastPoint = {
  time: string;
  temp: number;
  dewPoint: number | null;
  cloudCover: number | null;
  windSpeed: number | null;
  windDirection: number | null;
  precipChance: number | null;
  uvIndex: number | null;
};

type ForecastBaseline = {
  source: "twc" | "nws";
  localDate: string;
  issuedAt: string | null;
  capturedAt: string;
  forecastHigh: number | null;
  points: ForecastPoint[];
};

type ForecastCurrent = ForecastBaseline & {
  allPoints?: ForecastPoint[];
  dailyHighs?: Record<string, number | null>;
};

type Station = {
  stid: string;
  city: string;
  name: string;
  lat: number;
  lon: number;
  timezone: string | null;
  latest: WeatherRow | null;
  hf: WeatherRow[];
  official: WeatherRow[];
  sixHour: WeatherRow[];
  daily: WeatherRow[];
  hfAvailable: boolean;
  forecastBaseline: ForecastBaseline | null;
  forecastCurrent: ForecastCurrent | null;
};

type DashboardData = {
  updatedAt: string;
  stations: Station[];
  forecastSource?: string;
  forecastConfigured?: boolean;
  twsConfigured?: boolean;
  twsConnected?: boolean;
  twsStatus?: "connected" | "disconnected" | "not-configured";
  twsLastVerifiedAt?: string | null;
  twsCacheSeconds?: number;
  trajectoryModel?: string;
};

type NwsForecastPayload = {
  forecasts: Array<{ stid: string; baseline: ForecastBaseline }>;
};

type LocalPoint = { minute: number; temp: number };
type ObservedPoint = LocalPoint & { time: string };
type ResidualPoint = LocalPoint & { residual: number; time: string; row: WeatherRow; forecast: ForecastPoint | null };
type ProjectedPoint = LocalPoint & { kind: "forecast" };
type Mechanism = {
  key: "radiation" | "moisture" | "advection" | "precip" | "mixed" | "baseline";
  label: string;
  detail: string;
  evidence: string[];
};

function temp(value: number | null, digits = 1) {
  return value === null ? "—" : `${value.toFixed(digits)}°F`;
}

function value(value: number | null, suffix = "") {
  return value === null ? "—" : `${value.toFixed(1)}${suffix}`;
}

function timeLabel(iso: string, timezone: string | null) {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", timeZone: timezone ?? undefined }).format(new Date(iso));
  } catch {
    return iso;
  }
}

function shortTimeLabel(iso: string, timezone: string | null) {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: timezone ?? undefined }).format(new Date(iso));
  } catch {
    return "—";
  }
}

function localDateLabel(iso: string, timezone: string | null) {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      year: "numeric", month: "2-digit", day: "2-digit", timeZone: timezone ?? undefined,
    }).formatToParts(new Date(iso));
    const year = parts.find((part) => part.type === "year")?.value;
    const month = parts.find((part) => part.type === "month")?.value;
    const day = parts.find((part) => part.type === "day")?.value;
    return year && month && day ? `${year}-${month}-${day}` : "";
  } catch {
    return "";
  }
}

function minuteOfDay(iso: string, timezone: string | null) {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: timezone ?? undefined,
    }).formatToParts(new Date(iso));
    const hour = Number(parts.find((part) => part.type === "hour")?.value ?? "0");
    const minute = Number(parts.find((part) => part.type === "minute")?.value ?? "0");
    return hour * 60 + minute;
  } catch {
    return null;
  }
}

function windLabel(row: WeatherRow) {
  if (row.windSpeed === null && row.windDirection === null) return "—";
  const dir = row.windDirection === null ? "" : `${Math.round(row.windDirection)}°`;
  const speed = row.windSpeed === null ? "" : `${row.windSpeed.toFixed(0)} mph`;
  return [dir, speed].filter(Boolean).join(" · ");
}

function reportType(row: WeatherRow) {
  if (row.reportType) return row.reportType;
  if (row.raw?.startsWith("SPECI ")) return "SPECI";
  if (row.kind === "hf") return "HF";
  if (row.kind === "official") return "METAR";
  return "OTHER";
}

function pollDelayMs() {
  const minute = new Date().getMinutes();
  if (minute >= 49 || minute <= 2) return 2_000;
  if (minute >= 47) return 5_000;
  return 20_000;
}

function baselineAt(points: LocalPoint[], minute: number) {
  if (!points.length) return null;
  const exact = points.find((point) => point.minute === minute);
  if (exact) return exact.temp;
  for (let index = 0; index < points.length - 1; index += 1) {
    const left = points[index];
    const right = points[index + 1];
    if (minute >= left.minute && minute <= right.minute) {
      const width = right.minute - left.minute;
      if (width <= 0) return left.temp;
      const weight = (minute - left.minute) / width;
      return left.temp + (right.temp - left.temp) * weight;
    }
  }
  if (points.length >= 2 && minute < points[0].minute && points[0].minute - minute <= 90) {
    const left = points[0];
    const right = points[1];
    const width = right.minute - left.minute;
    if (width > 0) return left.temp + ((minute - left.minute) / width) * (right.temp - left.temp);
  }
  if (points.length >= 2 && minute > points[points.length - 1].minute && minute - points[points.length - 1].minute <= 90) {
    const left = points[points.length - 2];
    const right = points[points.length - 1];
    const width = right.minute - left.minute;
    if (width > 0) return right.temp + ((minute - right.minute) / width) * (right.temp - left.temp);
  }
  return null;
}

function nearestForecastPoint(points: ForecastPoint[], minute: number, timezone: string | null) {
  let best: ForecastPoint | null = null;
  let bestDistance = Infinity;
  for (const point of points) {
    const m = minuteOfDay(point.time, timezone);
    if (m === null) continue;
    const distance = Math.abs(m - minute);
    if (distance < bestDistance) {
      best = point;
      bestDistance = distance;
    }
  }
  return bestDistance <= 90 ? best : null;
}

function circularMinuteDistance(a: number, b: number) {
  const diff = Math.abs(a - b);
  return Math.min(diff, 60 - diff);
}

function inferRoutineMinute(rows: WeatherRow[], timezone: string | null) {
  const counts = new Map<number, number>();
  for (const row of rows) {
    if (row.temp === null) continue;
    const minute = minuteOfDay(row.time, timezone);
    if (minute === null) continue;
    const minutePart = minute % 60;
    counts.set(minutePart, (counts.get(minutePart) ?? 0) + 1);
  }
  let best: number | null = null;
  let bestCount = 0;
  for (const [minute, count] of counts) {
    if (count > bestCount) {
      best = minute;
      bestCount = count;
    }
  }
  return best;
}

function sampleVariance(values: number[]) {
  if (values.length < 2) return 1;
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length;
  return values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (values.length - 1);
}

function kalmanBiasState(points: ResidualPoint[]) {
  if (!points.length) return { bias: 0, variance: 4 };
  const residualValues = points.map((point) => point.residual);
  const differences = residualValues.slice(1).map((value, index) => value - residualValues[index]);
  const observationVariance = Math.max(0.35, Math.min(6, sampleVariance(residualValues)));
  const processVariance = Math.max(0.03, Math.min(1.5, sampleVariance(differences) * 0.15));
  let state = residualValues[0];
  let variance = observationVariance;
  for (let index = 1; index < residualValues.length; index += 1) {
    variance += processVariance;
    const gain = variance / (variance + observationVariance);
    state += gain * (residualValues[index] - state);
    variance = (1 - gain) * variance;
  }
  return { bias: state, variance };
}

function estimatePersistence(points: ResidualPoint[]) {
  if (points.length < 3) return 0.65;
  let numerator = 0;
  let denominator = 0;
  for (let index = 1; index < points.length; index += 1) {
    numerator += points[index].residual * points[index - 1].residual;
    denominator += points[index - 1].residual ** 2;
  }
  if (denominator < 0.05) return 0.5;
  return Math.max(0.15, Math.min(0.95, numerator / denominator));
}

function windVectorError(row: WeatherRow, forecast: ForecastPoint | null) {
  if (row.windSpeed === null || row.windDirection === null || forecast?.windSpeed === null || forecast?.windDirection === null || !forecast) return null;
  const toVector = (speed: number, direction: number) => {
    const radians = direction * Math.PI / 180;
    return { u: -speed * Math.sin(radians), v: -speed * Math.cos(radians) };
  };
  const observed = toVector(row.windSpeed, row.windDirection);
  const predicted = toVector(forecast.windSpeed as number, forecast.windDirection as number);
  return Math.hypot(observed.u - predicted.u, observed.v - predicted.v);
}

function diagnoseMechanism(residuals: ResidualPoint[], baselinePoints: LocalPoint[], sourceLabel: string): Mechanism {
  if (!residuals.length) return { key: "baseline", label: "No divergence yet", detail: "Waiting for an overlapping routine observation.", evidence: [] };
  const latest = residuals[residuals.length - 1];
  const forecast = latest.forecast;
  const evidence: string[] = [];

  const dewError = latest.row.dewPoint !== null && forecast?.dewPoint !== null && forecast
    ? latest.row.dewPoint - (forecast.dewPoint as number)
    : null;
  const cloudError = latest.row.cloudCover !== null && forecast?.cloudCover !== null && forecast
    ? latest.row.cloudCover - (forecast.cloudCover as number)
    : null;
  const windError = windVectorError(latest.row, forecast);
  const precipObserved = Boolean(latest.row.raw && /(?:^|\s)(?:\+|-)?(?:RA|DZ|TS|SHRA|VCTS)(?:\s|$)/.test(latest.row.raw));
  const precipForecast = forecast?.precipChance ?? null;
  const uv = forecast?.uvIndex ?? 0;

  let heatingRateError: number | null = null;
  if (residuals.length >= 2) {
    const prior = residuals[residuals.length - 2];
    const hours = (latest.minute - prior.minute) / 60;
    if (hours > 0) {
      const observedRate = (latest.temp - prior.temp) / hours;
      const baseLatest = baselineAt(baselinePoints, latest.minute);
      const basePrior = baselineAt(baselinePoints, prior.minute);
      if (baseLatest !== null && basePrior !== null) heatingRateError = observedRate - (baseLatest - basePrior) / hours;
    }
  }

  if (dewError !== null) evidence.push(`dew point ${dewError >= 0 ? "+" : ""}${dewError.toFixed(1)}°F vs ${sourceLabel}`);
  if (cloudError !== null) evidence.push(`cloud ${cloudError >= 0 ? "+" : ""}${Math.round(cloudError * 100)} pts vs ${sourceLabel}`);
  if (windError !== null) evidence.push(`wind-vector miss ${windError.toFixed(1)} mph`);
  if (heatingRateError !== null) evidence.push(`heating-rate miss ${heatingRateError >= 0 ? "+" : ""}${heatingRateError.toFixed(1)}°F/h`);
  if (precipObserved) evidence.push("precipitation/convection observed");

  const radiationScore = uv > 0 ? (Math.abs(cloudError ?? 0) * 2.2 + Math.abs(heatingRateError ?? 0) / 2) : 0;
  const moistureScore = Math.abs(dewError ?? 0) / 4;
  const advectionScore = (windError ?? 0) / 8;
  const precipScore = precipObserved ? 1.4 + Math.max(0, 40 - (precipForecast ?? 0)) / 60 : 0;
  const scores = [
    { key: "radiation" as const, score: radiationScore },
    { key: "moisture" as const, score: moistureScore },
    { key: "advection" as const, score: advectionScore },
    { key: "precip" as const, score: precipScore },
  ].sort((a, b) => b.score - a.score);

  if (scores[0].score < 0.55) {
    return { key: "baseline", label: "Mostly level bias", detail: "No single physical mismatch is strong enough to dominate; use the sequential bias correction only.", evidence };
  }
  if (scores[1].score >= scores[0].score * 0.8) {
    return { key: "mixed", label: "Mixed regime divergence", detail: "Several mechanisms are moving together. Do not extrapolate a single-cause story into the rest of the day.", evidence };
  }
  if (scores[0].key === "radiation") {
    const detail = cloudError !== null && cloudError > 0.12
      ? `Cloudier than ${sourceLabel} during usable daylight: short-wave heating runway is being suppressed.`
      : cloudError !== null && cloudError < -0.12
        ? `Clearer than ${sourceLabel} during usable daylight: more solar heating runway remains than the baseline expected.`
        : `The observed heating rate is diverging from ${sourceLabel} during daylight; cloud/radiation is the leading diagnostic.`;
    return { key: "radiation", label: "Cloud / solar divergence", detail, evidence };
  }
  if (scores[0].key === "moisture") {
    return {
      key: "moisture",
      label: "Moisture divergence",
      detail: dewError !== null && dewError > 0 ? `Boundary layer is moister than ${sourceLabel}. Treat the heating path as constrained until the moisture mismatch closes.` : `Boundary layer is drier than ${sourceLabel}. Sensible heating may run differently from the original curve.`,
      evidence,
    };
  }
  if (scores[0].key === "advection") {
    return { key: "advection", label: "Wind / advection divergence", detail: `The wind field differs materially from ${sourceLabel}. Local temperature bias can reset quickly if the wind direction or air mass changes.`, evidence };
  }
  return { key: "precip", label: "Precipitation regime break", detail: "Rain/convection arrived differently than forecast. Do not carry the pre-event temperature slope through the transition.", evidence };
}

function buildTrajectory(station: Station) {
  const frozen = station.forecastBaseline;
  const current = station.forecastCurrent?.points?.length ? station.forecastCurrent : frozen;
  if (!frozen?.points?.length || !current?.points?.length || !station.timezone) return null;

  const toLocalPoints = (points: ForecastPoint[]) => points
    .map((point) => {
      const minute = minuteOfDay(point.time, station.timezone);
      return minute === null ? null : { minute, temp: point.temp };
    })
    .filter((point): point is LocalPoint => point !== null)
    .sort((a, b) => a.minute - b.minute);

  const frozenPoints = toLocalPoints(frozen.points);
  const currentPoints = toLocalPoints(current.points);
  if (frozenPoints.length < 2 || currentPoints.length < 2) return null;

  const routineReports = station.official.filter((row) => reportType(row) === "METAR");
  const routineSource = routineReports.length ? routineReports : station.official;
  const routineMinute = inferRoutineMinute(routineSource, station.timezone);
  const todayOfficial = station.official
    .filter((row) => row.temp !== null && localDateLabel(row.time, station.timezone) === frozen.localDate)
    .map((row) => ({ row, minute: minuteOfDay(row.time, station.timezone) }))
    .filter((item): item is { row: WeatherRow; minute: number } => item.minute !== null)
    .sort((a, b) => a.minute - b.minute);
  const todayRoutine = routineSource
    .filter((row) => row.temp !== null && localDateLabel(row.time, station.timezone) === frozen.localDate)
    .map((row) => ({ row, minute: minuteOfDay(row.time, station.timezone) }))
    .filter((item): item is { row: WeatherRow; minute: number } => item.minute !== null)
    .sort((a, b) => a.minute - b.minute);
  const routineAnchors = todayRoutine.filter((item) => routineMinute === null || circularMinuteDistance(item.minute % 60, routineMinute) <= 4);

  const observedPoints: ObservedPoint[] = routineAnchors.map((item) => ({ minute: item.minute, temp: item.row.temp as number, time: item.row.time }));
  const precisePoints: ObservedPoint[] = todayOfficial.map((item) => ({ minute: item.minute, temp: item.row.temp as number, time: item.row.time }));

  // The adaptive lane is anchored to the latest TWC hourly path, not the frozen
  // pre-day path. TWC's live hourly endpoint usually starts near the current
  // hour, so only observations within the interpolation/extrapolation window
  // contribute to the live residual state.
  const residuals = routineAnchors
    .map((item) => {
      const base = baselineAt(currentPoints, item.minute);
      if (base === null || item.row.temp === null) return null;
      return {
        minute: item.minute,
        temp: item.row.temp,
        residual: item.row.temp - base,
        time: item.row.time,
        row: item.row,
        forecast: nearestForecastPoint(current.points, item.minute, station.timezone),
      };
    })
    .filter((point): point is ResidualPoint => point !== null);

  const frozenHourlyPeak = frozenPoints.reduce((best, point) => (point.temp > best.temp ? point : best), frozenPoints[0]);
  const currentHourlyPeak = currentPoints.reduce((best, point) => (point.temp > best.temp ? point : best), currentPoints[0]);
  const actualPeak = precisePoints.length ? precisePoints.reduce((best, point) => (point.temp > best.temp ? point : best), precisePoints[0]) : null;
  const sixHourRows = station.sixHour.filter((row) => row.high6 !== null && localDateLabel(row.time, station.timezone) === frozen.localDate);
  const sixHourPeak = sixHourRows.length ? sixHourRows.reduce((best, row) => ((row.high6 as number) > (best.high6 as number) ? row : best), sixHourRows[0]) : null;
  const mechanism = diagnoseMechanism(residuals, currentPoints, "latest TWC");

  if (!residuals.length) {
    return {
      frozenPoints, currentPoints, observedPoints, residuals: [] as ResidualPoint[], projected: [] as ProjectedPoint[],
      latestResidual: null as number | null, persistence: null as number | null, biasState: null as number | null,
      peak: actualPeak ? { minute: actualPeak.minute, temp: actualPeak.temp } : null,
      frozenHourlyPeak, currentHourlyPeak, actualPeak, sixHourPeak, routineMinute, mechanism,
    };
  }

  const latest = residuals[residuals.length - 1];
  const persistence = estimatePersistence(residuals);
  const state = kalmanBiasState(residuals);
  const projected: ProjectedPoint[] = [];
  for (const basePoint of currentPoints) {
    if (basePoint.minute <= latest.minute) continue;
    const hours = (basePoint.minute - latest.minute) / 60;
    const correction = state.bias * Math.pow(persistence, hours);
    projected.push({ minute: basePoint.minute, temp: basePoint.temp + correction, kind: "forecast" });
  }

  const candidates: LocalPoint[] = [
    ...observedPoints.map((point) => ({ minute: point.minute, temp: point.temp })),
    ...projected.map((point) => ({ minute: point.minute, temp: point.temp })),
  ];
  const peak = candidates.length ? candidates.reduce((best, point) => (point.temp > best.temp ? point : best), candidates[0]) : null;

  return {
    frozenPoints, currentPoints, observedPoints, residuals, projected,
    latestResidual: latest.residual, persistence, biasState: state.bias,
    peak, frozenHourlyPeak, currentHourlyPeak, actualPeak, sixHourPeak, routineMinute, mechanism,
  };
}

function clockLabel(minute: number) {
  const h24 = Math.floor(minute / 60) % 24;
  const m = minute % 60;
  const suffix = h24 >= 12 ? "PM" : "AM";
  const h = h24 % 12 || 12;
  return `${h}:${String(m).padStart(2, "0")} ${suffix}`;
}

function AdaptiveTrajectory({ station }: { station: Station }) {
  const model = useMemo(() => buildTrajectory(station), [station]);
  if (!model || !station.forecastBaseline) return null;

  const frozen = station.forecastBaseline;
  const current = station.forecastCurrent?.points?.length ? station.forecastCurrent : frozen;
  const sourceShort = frozen.source === "twc" ? "TWC" : "NWS";

  const daytimeFrozen = model.frozenPoints.filter((point) => point.minute >= 6 * 60 && point.minute <= 22 * 60);
  const daytimeCurrent = model.currentPoints.filter((point) => point.minute >= 6 * 60 && point.minute <= 22 * 60);
  if (daytimeFrozen.length < 2 || daytimeCurrent.length < 2) return null;
  const plottedObserved = model.observedPoints.filter((point) => point.minute >= 6 * 60 && point.minute <= 22 * 60);
  const plottedProjected = model.projected.filter((point) => point.minute >= 6 * 60 && point.minute <= 22 * 60);
  const allTemps = [
    ...daytimeFrozen.map((point) => point.temp),
    ...daytimeCurrent.map((point) => point.temp),
    ...plottedObserved.map((point) => point.temp),
    ...plottedProjected.map((point) => point.temp),
    ...(frozen.forecastHigh === null ? [] : [frozen.forecastHigh]),
    ...(current.forecastHigh === null ? [] : [current.forecastHigh]),
  ];
  const yMin = Math.floor(Math.min(...allTemps) - 2);
  const yMax = Math.ceil(Math.max(...allTemps) + 2);
  const xMin = 6 * 60;
  const xMax = 22 * 60;
  const width = 700;
  const height = 280;
  const pad = { left: 42, right: 16, top: 18, bottom: 34 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const x = (minute: number) => pad.left + ((minute - xMin) / (xMax - xMin)) * plotW;
  const y = (temperature: number) => pad.top + ((yMax - temperature) / Math.max(1, yMax - yMin)) * plotH;
  const line = (points: LocalPoint[]) => points.map((point) => `${x(point.minute)},${y(point.temp)}`).join(" ");
  const ticks = [6, 9, 12, 15, 18, 21];
  const latestResidualPoint = model.residuals.length ? model.residuals[model.residuals.length - 1] : null;
  const adaptive = plottedProjected.length && latestResidualPoint ? [{ minute: latestResidualPoint.minute, temp: latestResidualPoint.temp }, ...plottedProjected] : plottedProjected;
  const middleTick = Math.round((yMin + yMax) / 2);

  return (
    <section className={styles.trajectoryBlock}>
      <div className={styles.sectionTitle}>
        <div>
          <span>Daily trajectory</span>
          <h3>Frozen {sourceShort} vs latest {sourceShort} vs METAR vs adaptive</h3>
        </div>
        <small>Adaptive future uses the latest TWC path + live METAR bias</small>
      </div>

      <div className={styles.trajectoryStats}>
        <div>
          <span>Frozen TWC calendar high</span>
          <b>{frozen.forecastHigh !== null ? `${frozen.forecastHigh.toFixed(0)}°F` : "—"}</b>
          <small>{shortTimeLabel(frozen.capturedAt, station.timezone)} · hourly path {model.frozenHourlyPeak.temp.toFixed(0)}°</small>
        </div>
        <div>
          <span>Latest TWC calendar high</span>
          <b>{current.forecastHigh !== null ? `${current.forecastHigh.toFixed(0)}°F` : "—"}</b>
          <small>{shortTimeLabel(current.capturedAt, station.timezone)} · hourly path {model.currentHourlyPeak.temp.toFixed(0)}°</small>
        </div>
        <div>
          <span>METAR max so far</span>
          <b>{model.actualPeak ? `${model.actualPeak.temp.toFixed(0)}° · ${timeLabel(model.actualPeak.time, station.timezone)}` : "—"}</b>
          <small>{model.sixHourPeak?.high6 !== null && model.sixHourPeak?.high6 !== undefined ? `6h max report ${model.sixHourPeak.high6.toFixed(0)}°` : "No 6h max yet"}</small>
        </div>
        <div>
          <span>Adaptive hourly max</span>
          <b>{model.peak ? `${model.peak.temp.toFixed(1)}° · ${clockLabel(model.peak.minute)}` : "—"}</b>
          <small>{model.latestResidual === null ? "Waiting for live TWC/METAR overlap" : `latest METAR miss ${model.latestResidual >= 0 ? "+" : ""}${model.latestResidual.toFixed(1)}°`}</small>
        </div>
      </div>

      <div className={styles.trajectoryLegend}>
        <span><i className={styles.legendFrozen} />Frozen TWC · {shortTimeLabel(frozen.capturedAt, station.timezone)}</span>
        <span><i className={styles.legendCurrent} />Latest TWC · {shortTimeLabel(current.capturedAt, station.timezone)}</span>
        <span><i className={styles.legendObserved} />METAR observations</span>
        <span><i className={styles.legendAdaptive} />Adaptive = latest TWC + METAR bias</span>
        <span>{model.mechanism.label}</span>
      </div>

      <div className={styles.chartScroll}>
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${station.city} frozen and latest TWC hourly forecasts, METAR observations, and adaptive future trajectory`}>
          {[yMin, middleTick, yMax].map((tick) => (
            <g key={tick}>
              <line x1={pad.left} x2={width - pad.right} y1={y(tick)} y2={y(tick)} className={styles.gridLine} />
              <text x={pad.left - 8} y={y(tick) + 4} textAnchor="end" className={styles.axisText}>{tick}°</text>
            </g>
          ))}
          {ticks.map((hour) => <text key={hour} x={x(hour * 60)} y={height - 10} textAnchor="middle" className={styles.axisText}>{hour > 12 ? hour - 12 : hour}{hour >= 12 ? "p" : "a"}</text>)}
          <polyline points={line(daytimeFrozen)} className={styles.frozenLine} />
          <polyline points={line(daytimeCurrent)} className={styles.currentLine} />
          {plottedObserved.length > 1 && <polyline points={line(plottedObserved)} className={styles.observedLine} />}
          {adaptive.length > 1 && <polyline points={line(adaptive)} className={styles.adaptiveLine} />}
          {plottedObserved.map((point) => <circle key={`o-${point.minute}`} cx={x(point.minute)} cy={y(point.temp)} r="4" className={styles.observedDot} />)}
          {plottedProjected.map((point) => <circle key={`f-${point.minute}`} cx={x(point.minute)} cy={y(point.temp)} r="3.5" className={styles.adaptiveDot} />)}
        </svg>
      </div>

      <p className={styles.trajectoryNote}>
        <b>{model.mechanism.label}:</b> {model.mechanism.detail}{model.mechanism.evidence.length ? ` Evidence: ${model.mechanism.evidence.join(" · ")}.` : ""} Frozen TWC is preserved as the pre-day study benchmark. Latest TWC is the provider's newest hourly curve. METAR is observed reality. The adaptive line applies the live METAR residual to the latest TWC future; it does not alter or backfill either TWC curve. Calendar-day highs are shown separately because TWC's daily-high product can differ from the maximum value in its hourly path.
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
        <div><div className={styles.eyebrow}>{station.stid}</div><h2>{station.city}</h2><p>{station.name}</p></div>
        <div className={styles.sourceBadge}>{station.hfAvailable ? "5-min HF + official" : "METAR / SPECI"}</div>
      </header>

      <section className={styles.heroReadout}>
        <div><span>Latest</span><strong>{temp(station.latest?.temp ?? null, 0)}</strong><small>{station.latest ? `${timeLabel(station.latest.time, station.timezone)} · ${reportType(station.latest)}` : "No report"}</small></div>
        <div className={styles.miniStats}>
          <div><span>6h high</span><b>{temp(latest6?.high6 ?? null, 0)}</b></div>
          <div><span>6h low</span><b>{temp(latest6?.low6 ?? null, 0)}</b></div>
          <div><span>24h high</span><b>{temp(latest24?.high24 ?? null, 0)}</b></div>
          <div><span>RH</span><b>{value(station.latest?.rh ?? null, "%")}</b></div>
        </div>
      </section>

      {station.latest?.raw && <details className={styles.latestRaw}><summary>Latest raw report</summary><code>{station.latest.raw}</code></details>}
      <AdaptiveTrajectory station={station} />

      <section className={styles.sectionBlock}>
        <div className={styles.sectionTitle}><div><span>High frequency</span><h3>5-minute ASOS</h3></div><small>Whole °C wire → conservative floor °F</small></div>
        {station.hf.length ? (
          <TableShell><table><thead><tr><th>Time</th><th>Temp</th><th>Wind</th><th>RH</th><th>Alt</th></tr></thead><tbody>
            {station.hf.map((row) => <tr key={`${row.time}-${row.raw ?? "hf"}`}><td>{timeLabel(row.time, station.timezone)}</td><td className={styles.tempCell}>{temp(row.temp, 0)}</td><td>{windLabel(row)}</td><td>{value(row.rh, "%")}</td><td>{value(row.altimeter)}</td></tr>)}
          </tbody></table></TableShell>
        ) : <div className={styles.empty}>No 5-minute HF-ASOS rows are available for this station in Synoptic.</div>}
      </section>

      <section className={styles.sectionBlock}>
        <div className={styles.sectionTitle}><div><span>Official stream</span><h3>Hourly / SPECI reports</h3></div><small>Raw tenth °C T-group → decoded whole °F</small></div>
        <div className={styles.reportList}>
          {station.official.length ? station.official.map((row) => (
            <details className={styles.report} key={`${row.time}-${row.raw ?? "official"}`}><summary><time>{timeLabel(row.time, station.timezone)} · {reportType(row)}</time><b>{temp(row.temp, 0)}</b><span>{windLabel(row)}</span></summary><code>{row.raw ?? "No raw METAR text"}</code></details>
          )) : <div className={styles.empty}>No official reports in the current window.</div>}
        </div>
      </section>

      <section className={styles.sectionBlock}>
        <div className={styles.sectionTitle}><div><span>Max/min fields</span><h3>6-hour reports</h3></div><small>Tenths °C max/min groups → decoded whole °F</small></div>
        {station.sixHour.length ? (
          <TableShell><table><thead><tr><th>Time</th><th>6h high</th><th>6h low</th><th>Temp</th></tr></thead><tbody>
            {station.sixHour.map((row) => <tr key={`${row.time}-six`}><td>{timeLabel(row.time, station.timezone)}</td><td className={styles.highCell}>{temp(row.high6, 0)}</td><td>{temp(row.low6, 0)}</td><td>{temp(row.temp, 0)}</td></tr>)}
          </tbody></table></TableShell>
        ) : <div className={styles.empty}>No 6-hour max/min field in the current lookback yet.</div>}
      </section>
    </article>
  );
}

export function WeatherDashboardClient({
  selectedStid = "KNYC",
  embedded = false,
}: {
  selectedStid?: string;
  embedded?: boolean;
}) {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [response, nwsResponse] = await Promise.all([
        fetch(`/api/weather-dashboard?ts=${Date.now()}`, { cache: "no-store", headers: { "Cache-Control": "no-cache" } }),
        fetch(`/api/weather-dashboard/nws-forecast?ts=${Date.now()}`, { cache: "no-store", headers: { "Cache-Control": "no-cache" } }),
      ]);
      const next = await response.json() as DashboardData & { error?: string };
      const nws = await nwsResponse.json() as NwsForecastPayload & { error?: string };
      if (!response.ok) throw new Error(next.error ?? "Weather request failed");

      if (nwsResponse.ok && Array.isArray(nws.forecasts)) {
        const nwsByStid = new Map(nws.forecasts.map((item) => [item.stid, item.baseline]));
        next.stations = next.stations.map((station) => ({
          ...station,
          forecastBaseline: station.forecastBaseline ?? nwsByStid.get(station.stid) ?? null,
        }));
      }

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
    return () => { cancelled = true; if (timer !== undefined) window.clearTimeout(timer); };
  }, [load]);

  const visible = useMemo(() => {
    if (!data) return [];
    return data.stations.filter((station) => station.stid === selectedStid);
  }, [data, selectedStid]);

  const rapid = (() => { const minute = new Date().getMinutes(); return minute >= 49 || minute <= 2; })();

  return (
    <div className={`${styles.dashboard} ${embedded ? styles.embeddedDashboard : ""}`}>
      {!embedded && <header className={styles.topbar}><div><span>Mercury Edge</span><h1>Weather Reports</h1></div><button onClick={load} disabled={loading}>{loading ? "Loading…" : "Refresh"}</button></header>}
      {!embedded && (
        <nav className={styles.stationNav} aria-label="Weather stations">
          {data?.stations.map((station) => <button key={station.stid} className={selectedStid === station.stid ? styles.activeTab : ""}><b>{station.city}</b><small>{station.stid}</small></button>)}
        </nav>
      )}
      <div className={styles.statusLine}>
        <span className={error ? styles.badDot : styles.goodDot} />
        <span>{error ? error : data ? `${rapid ? "Rapid 2s official-report polling" : "Live"} · TWC trajectory · refreshed ${new Date(data.updatedAt).toLocaleTimeString()}` : "Connecting to weather feeds…"}</span>
        {data && (
          <span className={`${styles.sourceStatus} ${data.twsConnected ? styles.sourceStatusGood : styles.sourceStatusBad}`}>
            <span className={data.twsConnected ? styles.goodDot : styles.badDot} />
            Weather Company: {data.twsConnected ? "connected" : data.twsConfigured ? "not connected" : "not configured"}
          </span>
        )}
      </div>
      {data?.twsConnected && <div className={styles.notice}>Weather Company feed verified{data.twsLastVerifiedAt ? ` at ${new Date(data.twsLastVerifiedAt).toLocaleTimeString()}` : ""}. Provider data is cached server-side for {Math.round((data.twsCacheSeconds ?? 600) / 60)} minutes, so fast dashboard polling does not repeatedly consume API calls.</div>}
      {data?.twsConfigured && !data.twsConnected && <div className={styles.notice}>The Weather Company key is configured, but the latest provider fetch did not succeed. The existing trajectory stays isolated from that failure.</div>}
      {data && data.forecastConfigured === false && <div className={styles.notice}>The Weather Company trajectory is coded but no API key is configured on this deployment, so the trajectory panel will stay hidden until a server-side key is added.</div>}
      <div className={styles.notice}>HF-ASOS temperatures are transmitted in whole °C and remain a coarse conversion, so the conservative floor logic stays there. Official METAR T-groups and 6-hour/24-hour max-min groups are encoded in tenths °C and are decoded back to the nearest whole °F with half-up rounding. TWC forecast snapshots are archived in 15-minute buckets for walk-forward trajectory research.</div>
      <main className={styles.cards}>{visible.map((station) => <StationCard station={station} key={station.stid} />)}{!visible.length && !loading && !error && <div className={styles.empty}>No station data returned.</div>}</main>
    </div>
  );
}
