"use client";

import { useEffect, useMemo, useState } from "react";
import { MarketReactionPanel } from "@/components/MarketReactionPanel";
import deskStyles from "@/components/WeatherReactionDesk.module.css";

type WeatherRow = {
  time: string;
  temp: number | null;
  dewPoint: number | null;
  cloudCover: number | null;
  rh: number | null;
  windSpeed: number | null;
  windDirection: number | null;
  raw: string | null;
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
  source: "nws" | "twc";
  localDate: string;
  issuedAt: string | null;
  capturedAt: string;
  forecastHigh: number | null;
  points: ForecastPoint[];
};

type Station = {
  stid: string;
  city: string;
  name: string;
  timezone: string | null;
  official: WeatherRow[];
  hf?: WeatherRow[];
  forecastBaseline: ForecastBaseline | null;
};

type DashboardData = { stations: Station[]; updatedAt: string; forecastConfigured?: boolean };
type NwsForecastPayload = { forecasts: Array<{ stid: string; baseline: ForecastBaseline }> };
type Point = { minute: number; temp: number; time?: string };
type SeriesPoint = { minute: number; value: number; time?: string };
type Shock = { minute: number; time: string; delta: number };

function minuteOfDay(iso: string, timezone: string) {
  const parts = new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: timezone,
  }).formatToParts(new Date(iso));
  const h = Number(parts.find((part) => part.type === "hour")?.value ?? "0");
  const m = Number(parts.find((part) => part.type === "minute")?.value ?? "0");
  return h * 60 + m;
}

function localDate(iso: string, timezone: string) {
  const parts = new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: timezone,
  }).formatToParts(new Date(iso));
  const y = parts.find((part) => part.type === "year")?.value;
  const m = parts.find((part) => part.type === "month")?.value;
  const d = parts.find((part) => part.type === "day")?.value;
  return y && m && d ? `${y}-${m}-${d}` : "";
}

function interpolate(points: Point[], minute: number) {
  if (!points.length) return null;
  for (let i = 0; i < points.length - 1; i += 1) {
    const left = points[i];
    const right = points[i + 1];
    if (minute >= left.minute && minute <= right.minute) {
      const width = right.minute - left.minute;
      if (width <= 0) return left.temp;
      return left.temp + ((minute - left.minute) / width) * (right.temp - left.temp);
    }
  }
  const nearest = [...points].sort((a, b) => Math.abs(a.minute - minute) - Math.abs(b.minute - minute))[0];
  return nearest && Math.abs(nearest.minute - minute) <= 75 ? nearest.temp : null;
}

function interpolateSeries(points: SeriesPoint[], minute: number) {
  if (!points.length) return null;
  for (let i = 0; i < points.length - 1; i += 1) {
    const left = points[i];
    const right = points[i + 1];
    if (minute >= left.minute && minute <= right.minute) {
      const width = right.minute - left.minute;
      if (width <= 0) return left.value;
      return left.value + ((minute - left.minute) / width) * (right.value - left.value);
    }
  }
  const nearest = [...points].sort((a, b) => Math.abs(a.minute - minute) - Math.abs(b.minute - minute))[0];
  return nearest && Math.abs(nearest.minute - minute) <= 75 ? nearest.value : null;
}

function clock(minute: number) {
  const h24 = Math.floor(minute / 60) % 24;
  const m = minute % 60;
  const suffix = h24 >= 12 ? "PM" : "AM";
  return `${h24 % 12 || 12}:${String(m).padStart(2, "0")} ${suffix}`;
}

function chartModel(station: Station) {
  const timezone = station.timezone;
  if (!timezone) return null;
  const targetDate = station.forecastBaseline?.localDate ?? localDate(new Date().toISOString(), timezone);
  const anchor = (station.forecastBaseline?.points ?? [])
    .filter((point) => localDate(point.time, timezone) === targetDate)
    .map((point) => ({ minute: minuteOfDay(point.time, timezone), temp: point.temp }))
    .sort((a, b) => a.minute - b.minute);
  const actual = station.official
    .filter((row) => row.temp !== null && localDate(row.time, timezone) === targetDate)
    .map((row) => ({ minute: minuteOfDay(row.time, timezone), temp: row.temp as number, time: row.time }))
    .sort((a, b) => a.minute - b.minute);
  const shocks: Shock[] = [];
  if (anchor.length) {
    let previousResidual: number | null = null;
    for (const point of actual) {
      const expected = interpolate(anchor, point.minute);
      if (expected === null) continue;
      const residual = point.temp - expected;
      if (previousResidual !== null) {
        const delta = residual - previousResidual;
        if (Math.abs(delta) >= 0.9) shocks.push({ minute: point.minute, time: point.time ?? "", delta });
      } else if (Math.abs(residual) >= 1.5) {
        shocks.push({ minute: point.minute, time: point.time ?? "", delta: residual });
      }
      previousResidual = residual;
    }
  }
  return { targetDate, anchor, actual, shocks };
}

function AnchorChart({ station }: { station: Station }) {
  const model = useMemo(() => chartModel(station), [station]);
  if (!station.timezone) return <div className={deskStyles.empty}>Station timezone is unavailable.</div>;
  if (!model || (!model.anchor.length && !model.actual.length)) {
    return <div className={deskStyles.empty}>No observations are available for today yet.</div>;
  }

  const xMin = 6 * 60;
  const xMax = 22 * 60;
  const visibleAnchor = model.anchor.filter((point) => point.minute >= xMin && point.minute <= xMax);
  const visibleActual = model.actual.filter((point) => point.minute >= xMin && point.minute <= xMax);
  const temperatures = [...visibleAnchor, ...visibleActual].map((point) => point.temp);
  if (!temperatures.length) return <div className={deskStyles.empty}>No chartable readings are available between 6 AM and 10 PM for {model.targetDate}.</div>;

  const yMin = Math.floor(Math.min(...temperatures) - 2);
  const yMax = Math.ceil(Math.max(...temperatures) + 2);
  const width = 700;
  const height = 255;
  const pad = { left: 42, right: 18, top: 18, bottom: 34 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const x = (minute: number) => pad.left + ((minute - xMin) / (xMax - xMin)) * plotW;
  const y = (temperature: number) => pad.top + ((yMax - temperature) / Math.max(1, yMax - yMin)) * plotH;
  const polyline = (points: Point[]) => points.map((point) => `${x(point.minute)},${y(point.temp)}`).join(" ");
  const ticks = [6, 9, 12, 15, 18, 21];
  const yTicks = [yMin, Math.round((yMin + yMax) / 2), yMax];
  const hasAnchor = visibleAnchor.length > 0;

  return (
    <section className={deskStyles.anchor}>
      <div className={deskStyles.title}>
        <div>
          <span>Anchor + response · {model.targetDate}</span>
          <h3>{hasAnchor ? "Frozen NWS path vs actual" : "Observed temperature path · NWS anchor loading"}</h3>
        </div>
        <small>{hasAnchor ? `NWS high ${station.forecastBaseline?.forecastHigh === null || station.forecastBaseline?.forecastHigh === undefined ? "—" : `${station.forecastBaseline.forecastHigh.toFixed(0)}°F`}` : "NWS forecast unavailable"}</small>
      </div>
      <div className={deskStyles.legend}>
        {hasAnchor && <span><i className={deskStyles.anchorKey} />Frozen NWS anchor</span>}
        <span><i className={deskStyles.actualKey} />Observed METAR</span>
        {hasAnchor && <span><i className={deskStyles.shockKey} />Temperature residual shock ≥0.9°F</span>}
      </div>
      <div className={deskStyles.chart}>
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${station.city} temperature path for ${model.targetDate}`}>
          {yTicks.map((tick) => (
            <g key={tick}>
              <line x1={pad.left} x2={width - pad.right} y1={y(tick)} y2={y(tick)} className={deskStyles.grid} />
              <text x={pad.left - 8} y={y(tick) + 4} textAnchor="end" className={deskStyles.axis}>{tick}°</text>
            </g>
          ))}
          {ticks.map((hour) => <text key={hour} x={x(hour * 60)} y={height - 10} textAnchor="middle" className={deskStyles.axis}>{hour > 12 ? hour - 12 : hour}{hour >= 12 ? "p" : "a"}</text>)}
          {hasAnchor && <polyline points={polyline(visibleAnchor)} className={deskStyles.anchorLine} />}
          {visibleActual.length > 1 && <polyline points={polyline(visibleActual)} className={deskStyles.actualLine} />}
          {visibleActual.map((point) => <circle key={point.time} cx={x(point.minute)} cy={y(point.temp)} r="4" className={deskStyles.actualDot} />)}
          {model.shocks.filter((shock) => shock.minute >= xMin && shock.minute <= xMax).map((shock) => (
            <g key={shock.time}>
              <line x1={x(shock.minute)} x2={x(shock.minute)} y1={pad.top} y2={height - pad.bottom} className={deskStyles.shockLine} />
              <text x={x(shock.minute) + 3} y={pad.top + 10} className={deskStyles.shockText}>{clock(shock.minute)} {shock.delta >= 0 ? "+" : ""}{shock.delta.toFixed(1)}°</text>
            </g>
          ))}
        </svg>
      </div>
      <p className={deskStyles.note}>The NWS path is frozen for the day so actual weather can be judged against the conditions the forecast expected, rather than against a forecast that keeps moving after the fact.</p>
    </section>
  );
}

function windVectorDifference(actualSpeed: number, actualDirection: number, expectedSpeed: number, expectedDirection: number) {
  const vector = (speed: number, direction: number) => {
    const radians = direction * Math.PI / 180;
    return { x: -speed * Math.sin(radians), y: -speed * Math.cos(radians) };
  };
  const actual = vector(actualSpeed, actualDirection);
  const expected = vector(expectedSpeed, expectedDirection);
  return Math.hypot(actual.x - expected.x, actual.y - expected.y);
}

function buildDriverSeries(station: Station) {
  const timezone = station.timezone;
  const baseline = station.forecastBaseline;
  if (!timezone || !baseline?.points.length) return null;
  const date = baseline.localDate;
  const makeForecast = (getter: (point: ForecastPoint) => number | null): SeriesPoint[] => baseline.points
    .filter((point) => localDate(point.time, timezone) === date)
    .map((point) => ({ minute: minuteOfDay(point.time, timezone), value: getter(point) }))
    .filter((point): point is { minute: number; value: number } => point.value !== null && Number.isFinite(point.value))
    .sort((a, b) => a.minute - b.minute);

  const expectedTemp = makeForecast((point) => point.temp);
  const expectedDew = makeForecast((point) => point.dewPoint);
  const expectedCloud = makeForecast((point) => point.cloudCover === null ? null : point.cloudCover * 100);
  const expectedWindSpeed = makeForecast((point) => point.windSpeed);
  const expectedWindDirection = makeForecast((point) => point.windDirection);
  const rows = [...(station.hf ?? []), ...station.official]
    .filter((row) => localDate(row.time, timezone) === date)
    .sort((a, b) => new Date(a.time).getTime() - new Date(b.time).getTime());
  const deduped = rows.filter((row, index) => index === rows.length - 1 || rows[index + 1].time !== row.time);

  const cloud: SeriesPoint[] = [];
  const dew: SeriesPoint[] = [];
  const wind: SeriesPoint[] = [];
  const temp: SeriesPoint[] = [];
  for (const row of deduped) {
    const minute = minuteOfDay(row.time, timezone);
    const expectedT = interpolateSeries(expectedTemp, minute);
    if (row.temp !== null && expectedT !== null) temp.push({ minute, value: row.temp - expectedT, time: row.time });
    const expectedD = interpolateSeries(expectedDew, minute);
    if (row.dewPoint !== null && expectedD !== null) dew.push({ minute, value: row.dewPoint - expectedD, time: row.time });
    const expectedC = interpolateSeries(expectedCloud, minute);
    if (row.cloudCover !== null && expectedC !== null) cloud.push({ minute, value: row.cloudCover * 100 - expectedC, time: row.time });
    const expectedWs = interpolateSeries(expectedWindSpeed, minute);
    const expectedWd = interpolateSeries(expectedWindDirection, minute);
    if (row.windSpeed !== null && row.windDirection !== null && expectedWs !== null && expectedWd !== null) {
      wind.push({ minute, value: windVectorDifference(row.windSpeed, row.windDirection, expectedWs, expectedWd), time: row.time });
    }
  }
  return { cloud, dew, wind, temp };
}

function ResidualStrip({
  title,
  explanation,
  points,
  unit,
  zeroCentered = true,
  minScale,
  showClock = false,
}: {
  title: string;
  explanation: string;
  points: SeriesPoint[];
  unit: string;
  zeroCentered?: boolean;
  minScale: number;
  showClock?: boolean;
}) {
  const xMin = 6 * 60;
  const xMax = 22 * 60;
  const visible = points.filter((point) => point.minute >= xMin && point.minute <= xMax);
  const width = 700;
  const height = showClock ? 96 : 78;
  const pad = { left: 42, right: 18, top: 8, bottom: showClock ? 24 : 8 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const x = (minute: number) => pad.left + ((minute - xMin) / (xMax - xMin)) * plotW;
  const maxObserved = Math.max(minScale, ...visible.map((point) => Math.abs(point.value)));
  const yMin = zeroCentered ? -maxObserved : 0;
  const yMax = maxObserved;
  const y = (value: number) => pad.top + ((yMax - value) / Math.max(0.001, yMax - yMin)) * plotH;
  const ticks = [6, 9, 12, 15, 18, 21];
  const current = visible[visible.length - 1] ?? null;
  const line = visible.map((point) => `${x(point.minute)},${y(point.value)}`).join(" ");
  const signed = current ? `${current.value > 0 ? "+" : ""}${current.value.toFixed(unit === "pp" ? 0 : 1)}${unit}` : "—";

  return (
    <div className={deskStyles.driverRow}>
      <div className={deskStyles.driverRowHeader}>
        <div><b>{title}</b><small>{explanation}</small></div>
        <strong>{signed}</strong>
      </div>
      <div className={deskStyles.driverChart}>
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${title} through the day`}>
          {ticks.map((hour) => (
            <line key={hour} x1={x(hour * 60)} x2={x(hour * 60)} y1={pad.top} y2={height - pad.bottom} className={deskStyles.driverGridLine} />
          ))}
          <line x1={pad.left} x2={width - pad.right} y1={y(0)} y2={y(0)} className={deskStyles.driverZero} />
          {line && <polyline points={line} className={deskStyles.driverLine} />}
          {visible.map((point) => <circle key={`${point.time}-${point.minute}`} cx={x(point.minute)} cy={y(point.value)} r="2.3" className={deskStyles.driverDot} />)}
          {showClock && ticks.map((hour) => (
            <text key={`clock-${hour}`} x={x(hour * 60)} y={height - 7} textAnchor="middle" className={deskStyles.driverAxis}>{hour > 12 ? hour - 12 : hour}{hour >= 12 ? "p" : "a"}</text>
          ))}
        </svg>
      </div>
    </div>
  );
}

function DriverPanel({ station }: { station: Station }) {
  const series = useMemo(() => buildDriverSeries(station), [station]);
  if (!series || !station.forecastBaseline) return null;
  return (
    <section className={deskStyles.driverPanel}>
      <div className={deskStyles.driverHeader}>
        <div>
          <span>Leading-condition departures</span>
          <h3>What is moving away from NWS before temperature does?</h3>
        </div>
        <small>All strips share the same clock. The center line means “tracking NWS.”</small>
      </div>
      <ResidualStrip title="Cloud excess" explanation="Up = cloudier than NWS · down = clearer" points={series.cloud} unit="pp" minScale={20} />
      <ResidualStrip title="Airflow departure" explanation="Up = wind vector increasingly unlike NWS" points={series.wind} unit=" mph" minScale={5} zeroCentered={false} />
      <ResidualStrip title="Dew-point residual" explanation="Up = moister than NWS · down = drier" points={series.dew} unit="°F" minScale={3} />
      <ResidualStrip title="Temperature residual" explanation="Response: actual minus the frozen NWS path" points={series.temp} unit="°F" minScale={2} showClock />
      <p className={deskStyles.driverNote}>The useful pattern is lead/lag: a driver strip begins departing first, then the temperature-residual strip bends in the same episode. No composite score is imposed yet, so repeated days can teach which shapes actually matter at each station.</p>
    </section>
  );
}

export function WeatherReactionDesk() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [selected, setSelected] = useState("KNYC");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [response, nwsResponse] = await Promise.all([
          fetch(`/api/weather-dashboard?reactionDesk=${Date.now()}`, { cache: "no-store" }),
          fetch(`/api/weather-dashboard/nws-forecast?reactionDesk=${Date.now()}`, { cache: "no-store" }),
        ]);
        const payload = await response.json();
        const nwsPayload = await nwsResponse.json() as NwsForecastPayload & { error?: string };
        if (!response.ok) throw new Error(payload.error ?? "Weather dashboard feed failed");
        if (!nwsResponse.ok) throw new Error(nwsPayload.error ?? "NWS forecast feed failed");
        const byStid = new Map(nwsPayload.forecasts.map((item) => [item.stid, item.baseline]));
        const merged: DashboardData = {
          ...payload,
          stations: payload.stations.map((station: Station) => ({
            ...station,
            forecastBaseline: byStid.get(station.stid) ?? null,
          })),
        };
        if (!cancelled) { setData(merged); setError(null); }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Unable to load reaction desk");
      }
    };
    void load();
    const timer = window.setInterval(load, 30_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  const station = data?.stations.find((item) => item.stid === selected) ?? data?.stations[0] ?? null;
  if (error) return <section className={deskStyles.desk}><div className={deskStyles.empty}>{error}</div></section>;
  if (!station) return <section className={deskStyles.desk}><div className={deskStyles.empty}>Loading NWS ↔ Kalshi reaction desk…</div></section>;

  return (
    <section className={deskStyles.desk}>
      <header className={deskStyles.header}>
        <div><span>Mercury reaction desk</span><h2>NWS anchor ↔ observed drivers ↔ Kalshi</h2></div>
        <small>One shared clock: frozen NWS expectation, observed driver departures, temperature response and Kalshi bucket repricing.</small>
      </header>
      <nav className={deskStyles.tabs} aria-label="Reaction desk stations">
        {data?.stations.map((item) => (
          <button key={item.stid} className={item.stid === station.stid ? deskStyles.active : ""} onClick={() => setSelected(item.stid)}>{item.city}</button>
        ))}
      </nav>
      <AnchorChart station={station} />
      <DriverPanel station={station} />
      <MarketReactionPanel
        stid={station.stid}
        timezone={station.timezone ?? "UTC"}
        baseline={station.forecastBaseline}
        official={station.official}
      />
    </section>
  );
}
