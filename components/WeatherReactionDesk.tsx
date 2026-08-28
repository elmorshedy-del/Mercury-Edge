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
  source: "twc";
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
  forecastBaseline: ForecastBaseline | null;
};

type DashboardData = { stations: Station[]; updatedAt: string; forecastConfigured?: boolean };
type Point = { minute: number; temp: number; time?: string };
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
          <span>Anchor + shocks · {model.targetDate}</span>
          <h3>{hasAnchor ? "Immutable pre-day TWC path vs actual" : "Observed temperature path · TWC anchor pending"}</h3>
        </div>
        <small>{hasAnchor ? `TWC high ${station.forecastBaseline?.forecastHigh === null || station.forecastBaseline?.forecastHigh === undefined ? "—" : `${station.forecastBaseline.forecastHigh.toFixed(0)}°F`}` : "TWC_API_KEY not configured"}</small>
      </div>
      <div className={deskStyles.legend}>
        {hasAnchor && <span><i className={deskStyles.anchorKey} />Pre-day TWC anchor</span>}
        <span><i className={deskStyles.actualKey} />Observed METAR</span>
        {hasAnchor && <span><i className={deskStyles.shockKey} />Residual shock ≥0.9°F</span>}
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
      <p className={deskStyles.note}>{hasAnchor ? "The TWC anchor remains immutable; later TWC revisions are separate market-information events on the Kalshi panel." : "Observed temperatures remain visible now. The immutable TWC anchor and residual-shock layer will appear automatically after TWC_API_KEY is configured."}</p>
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
        const response = await fetch(`/api/weather-dashboard?reactionDesk=${Date.now()}`, { cache: "no-store" });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error ?? "Weather dashboard feed failed");
        if (!cancelled) { setData(payload); setError(null); }
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
  if (!station) return <section className={deskStyles.desk}><div className={deskStyles.empty}>Loading TWC ↔ Kalshi reaction desk…</div></section>;

  return (
    <section className={deskStyles.desk}>
      <header className={deskStyles.header}>
        <div><span>Mercury reaction desk</span><h2>TWC anchor ↔ Kalshi repricing</h2></div>
        <small>Shared clock for forecast anchor, observed shocks, TWC revisions, Kalshi bucket prices, HRRR and NBM context.</small>
      </header>
      <nav className={deskStyles.tabs} aria-label="Reaction desk stations">
        {data?.stations.map((item) => (
          <button key={item.stid} className={item.stid === station.stid ? deskStyles.active : ""} onClick={() => setSelected(item.stid)}>{item.city}</button>
        ))}
      </nav>
      <AnchorChart station={station} />
      <MarketReactionPanel
        stid={station.stid}
        timezone={station.timezone ?? "UTC"}
        baseline={station.forecastBaseline}
        official={station.official}
      />
    </section>
  );
}
