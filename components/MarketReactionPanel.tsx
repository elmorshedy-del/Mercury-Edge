"use client";

import { useEffect, useMemo, useState } from "react";
import styles from "@/app/weather-dashboard/weather-dashboard.module.css";
import reactionStyles from "@/components/MarketReactionPanel.module.css";
import {
  pointAtOrBefore,
  pointNear,
  quoteMid,
  strongestBucketMove,
  type MarketCenterPoint,
  type MarketSeries,
} from "@/lib/weather/market-reaction";

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

type MarketPayload = {
  stid: string;
  date: string;
  timezone: string;
  seriesTicker: string;
  eventTicker: string | null;
  eventStatus?: string;
  markets: Array<MarketSeries & { latestMid: number | null }>;
  marketCenter: MarketCenterPoint[];
  leader: { ticker: string; label: string; probability: number } | null;
  twcRevisions: Array<{ time: string; forecastHigh: number; delta: number | null }>;
  updatedAt?: string;
};

type ModelPoint = {
  time: string;
  temp: number | null;
  dewPoint: number | null;
  cloudCover: number | null;
  windSpeed: number | null;
  windDirection: number | null;
  precipitation: number | null;
  shortwaveRadiation: number | null;
};

type ModelPayload = {
  hrrr: { points: ModelPoint[] };
  nbm: { points: ModelPoint[] };
  role: string;
};

type Shock = {
  time: string;
  minute: number;
  label: string;
  residual: number;
  deltaResidual: number | null;
  kind: "observation" | "precip";
};

function localDateLabel(iso: string, timezone: string) {
  const parts = new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: timezone,
  }).formatToParts(new Date(iso));
  const year = parts.find((part) => part.type === "year")?.value;
  const month = parts.find((part) => part.type === "month")?.value;
  const day = parts.find((part) => part.type === "day")?.value;
  return year && month && day ? `${year}-${month}-${day}` : "";
}

function minuteOfDay(iso: string, timezone: string) {
  const parts = new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: timezone,
  }).formatToParts(new Date(iso));
  const hour = Number(parts.find((part) => part.type === "hour")?.value ?? "0");
  const minute = Number(parts.find((part) => part.type === "minute")?.value ?? "0");
  return hour * 60 + minute;
}

function baselineAt(points: Array<{ minute: number; temp: number }>, minute: number) {
  if (!points.length) return null;
  for (let index = 0; index < points.length - 1; index += 1) {
    const left = points[index];
    const right = points[index + 1];
    if (minute >= left.minute && minute <= right.minute) {
      const width = right.minute - left.minute;
      if (width <= 0) return left.temp;
      return left.temp + ((minute - left.minute) / width) * (right.temp - left.temp);
    }
  }
  const nearest = [...points].sort((a, b) => Math.abs(a.minute - minute) - Math.abs(b.minute - minute))[0];
  return nearest && Math.abs(nearest.minute - minute) <= 75 ? nearest.temp : null;
}

function clockLabel(minute: number) {
  const h24 = Math.floor(minute / 60) % 24;
  const m = minute % 60;
  const suffix = h24 >= 12 ? "PM" : "AM";
  const h = h24 % 12 || 12;
  return `${h}:${String(m).padStart(2, "0")} ${suffix}`;
}

function buildShocks(official: WeatherRow[], baseline: ForecastBaseline | null, timezone: string): Shock[] {
  if (!baseline?.points.length) return [];
  const base = baseline.points
    .map((point) => ({ minute: minuteOfDay(point.time, timezone), temp: point.temp }))
    .sort((a, b) => a.minute - b.minute);
  const rows = official
    .filter((row) => row.temp !== null && localDateLabel(row.time, timezone) === baseline.localDate)
    .map((row) => ({ row, minute: minuteOfDay(row.time, timezone) }))
    .sort((a, b) => a.minute - b.minute);

  const shocks: Shock[] = [];
  let priorResidual: number | null = null;
  let priorPrecip = false;
  for (const item of rows) {
    const expected = baselineAt(base, item.minute);
    if (expected === null || item.row.temp === null) continue;
    const residual = item.row.temp - expected;
    const deltaResidual = priorResidual === null ? null : residual - priorResidual;
    const precip = Boolean(item.row.raw && /(?:^|\s)(?:\+|-)?(?:RA|DZ|TS|SHRA|VCTS)(?:\s|$)/.test(item.row.raw));
    const precipStart = precip && !priorPrecip;
    const largeChange = deltaResidual !== null && Math.abs(deltaResidual) >= 0.9;
    const largeLevel = priorResidual === null && Math.abs(residual) >= 1.5;
    if (largeChange || largeLevel || precipStart) {
      const changeText = deltaResidual === null
        ? `${residual >= 0 ? "+" : ""}${residual.toFixed(1)}°F vs TWC`
        : `${deltaResidual >= 0 ? "+" : ""}${deltaResidual.toFixed(1)}°F shock`;
      shocks.push({
        time: item.row.time,
        minute: item.minute,
        label: precipStart ? `Rain/convection · ${changeText}` : `Temperature · ${changeText}`,
        residual,
        deltaResidual,
        kind: precipStart ? "precip" : "observation",
      });
    }
    priorResidual = residual;
    priorPrecip = precip;
  }
  return shocks;
}

function modelPointForMinute(points: ModelPoint[], date: string, minute: number) {
  const hour = Math.round(minute / 60);
  const target = `${date}T${String(Math.min(23, Math.max(0, hour))).padStart(2, "0")}:00`;
  return points.find((point) => point.time === target) ?? null;
}

const PALETTE = ["#234f3e", "#447a66", "#7a9348", "#bd8a3c", "#a45f44", "#7c5b8f", "#426a91", "#89585f", "#597a81"];

export function MarketReactionPanel({
  stid,
  timezone,
  baseline,
  official,
}: {
  stid: string;
  timezone: string;
  baseline: ForecastBaseline | null;
  official: WeatherRow[];
}) {
  const [market, setMarket] = useState<MarketPayload | null>(null);
  const [models, setModels] = useState<ModelPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const date = baseline?.localDate ?? localDateLabel(new Date().toISOString(), timezone);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [marketResponse, modelResponse] = await Promise.all([
          fetch(`/api/weather-dashboard/market-reaction?stid=${encodeURIComponent(stid)}&date=${encodeURIComponent(date)}`, { cache: "no-store" }),
          fetch(`/api/weather-dashboard/model-context?stid=${encodeURIComponent(stid)}`, { cache: "no-store" }),
        ]);
        const marketJson = await marketResponse.json();
        const modelJson = await modelResponse.json();
        if (!marketResponse.ok) throw new Error(marketJson.error ?? "Kalshi reaction feed failed");
        if (!cancelled) {
          setMarket(marketJson);
          setModels(modelResponse.ok ? modelJson : null);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Unable to load reaction chart");
      }
    };
    void load();
    const timer = window.setInterval(load, 30_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [stid, date]);

  const shocks = useMemo(() => buildShocks(official, baseline, timezone), [official, baseline, timezone]);
  const reactions = useMemo(() => {
    if (!market) return [];
    return shocks.slice(-4).reverse().map((shock) => {
      const t = new Date(shock.time).getTime();
      const pre = pointAtOrBefore(market.marketCenter, t - 60_000);
      const p5 = pointNear(market.marketCenter, t + 5 * 60_000, 4 * 60_000) ?? pointAtOrBefore(market.marketCenter, t + 5 * 60_000);
      const p15 = pointNear(market.marketCenter, t + 15 * 60_000, 4 * 60_000) ?? pointAtOrBefore(market.marketCenter, t + 15 * 60_000);
      const strongest = strongestBucketMove(market.markets, t - 60_000, t + 15 * 60_000);
      return {
        shock,
        move5: pre && p5 ? p5.value - pre.value : null,
        move15: pre && p15 ? p15.value - pre.value : null,
        strongest,
      };
    });
  }, [market, shocks]);

  if (error) return <div className={reactionStyles.reactionError}>Kalshi reaction chart: {error}</div>;
  if (!market) return <div className={reactionStyles.reactionLoading}>Loading synchronized Kalshi reaction…</div>;
  if (!market.eventTicker || !market.markets.length) {
    return <div className={reactionStyles.reactionError}>No Kalshi weather event was found for {date}.</div>;
  }

  const xMin = 6 * 60;
  const xMax = 22 * 60;
  const width = 700;
  const height = 255;
  const pad = { left: 42, right: 18, top: 18, bottom: 34 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const x = (minute: number) => pad.left + ((minute - xMin) / (xMax - xMin)) * plotW;
  const y = (probability: number) => pad.top + ((1 - probability) * plotH);
  const ticks = [6, 9, 12, 15, 18, 21];
  const yTicks = [0, 0.25, 0.5, 0.75, 1];
  const visibleMarkets = market.markets
    .filter((item) => item.quotes.length)
    .sort((a, b) => (a.lower ?? -999) - (b.lower ?? -999));
  const latestCenter = market.marketCenter[market.marketCenter.length - 1] ?? null;
  const latestObs = official
    .filter((row) => row.temp !== null && localDateLabel(row.time, timezone) === date)
    .sort((a, b) => new Date(b.time).getTime() - new Date(a.time).getTime())[0] ?? null;
  const latestMinute = latestObs ? minuteOfDay(latestObs.time, timezone) : minuteOfDay(new Date().toISOString(), timezone);
  const twcAtLatest = baseline
    ? baselineAt(baseline.points.map((point) => ({ minute: minuteOfDay(point.time, timezone), temp: point.temp })), latestMinute)
    : null;
  const hrrrNow = models ? modelPointForMinute(models.hrrr.points, date, latestMinute) : null;
  const nbmNow = models ? modelPointForMinute(models.nbm.points, date, latestMinute) : null;

  return (
    <section className={reactionStyles.reactionBlock}>
      <div className={styles.sectionTitle}>
        <div>
          <span>Market reaction</span>
          <h3>Kalshi synchronized to TWC shocks</h3>
        </div>
        <small>{market.seriesTicker} · {market.eventTicker}</small>
      </div>

      <div className={reactionStyles.reactionStats}>
        <div><span>Market center</span><b>{latestCenter ? `${latestCenter.value.toFixed(2)}°F` : "—"}</b></div>
        <div><span>Leading bucket</span><b>{market.leader ? `${market.leader.label} · ${(market.leader.probability * 100).toFixed(0)}%` : "—"}</b></div>
        <div><span>HRRR vs TWC</span><b>{hrrrNow?.temp !== null && hrrrNow && twcAtLatest !== null ? `${(hrrrNow.temp - twcAtLatest).toFixed(1)}°F` : "—"}</b></div>
        <div><span>NBM vs TWC</span><b>{nbmNow?.temp !== null && nbmNow && twcAtLatest !== null ? `${(nbmNow.temp - twcAtLatest).toFixed(1)}°F` : "—"}</b></div>
      </div>

      <div className={reactionStyles.reactionLegend}>
        {visibleMarkets.map((item, index) => (
          <span key={item.ticker}><i style={{ background: PALETTE[index % PALETTE.length] }} />{item.label}</span>
        ))}
        <span><i className={reactionStyles.shockKey} />Observed shock</span>
        <span><i className={reactionStyles.revisionKey} />TWC revision</span>
      </div>

      <div className={styles.chartScroll}>
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${stid} Kalshi weather bucket prices synchronized to observed and TWC forecast shocks`}>
          {yTicks.map((tick) => (
            <g key={tick}>
              <line x1={pad.left} x2={width - pad.right} y1={y(tick)} y2={y(tick)} className={styles.gridLine} />
              <text x={pad.left - 8} y={y(tick) + 4} textAnchor="end" className={styles.axisText}>{Math.round(tick * 100)}%</text>
            </g>
          ))}
          {ticks.map((hour) => (
            <text key={hour} x={x(hour * 60)} y={height - 10} textAnchor="middle" className={styles.axisText}>
              {hour > 12 ? hour - 12 : hour}{hour >= 12 ? "p" : "a"}
            </text>
          ))}

          {shocks.filter((shock) => shock.minute >= xMin && shock.minute <= xMax).map((shock) => (
            <g key={`shock-${shock.time}`}>
              <line x1={x(shock.minute)} x2={x(shock.minute)} y1={pad.top} y2={height - pad.bottom} className={reactionStyles.shockLine} />
              <text x={x(shock.minute) + 3} y={pad.top + 10} className={reactionStyles.shockText}>{clockLabel(shock.minute)}</text>
            </g>
          ))}
          {market.twcRevisions.map((revision) => {
            const minute = minuteOfDay(revision.time, timezone);
            if (minute < xMin || minute > xMax || localDateLabel(revision.time, timezone) !== date) return null;
            return (
              <line
                key={`revision-${revision.time}`}
                x1={x(minute)}
                x2={x(minute)}
                y1={pad.top}
                y2={height - pad.bottom}
                className={reactionStyles.revisionLine}
              />
            );
          })}

          {visibleMarkets.map((item, index) => {
            const points = item.quotes
              .map((quote) => ({ minute: minuteOfDay(quote.time, timezone), probability: quoteMid(quote) }))
              .filter((point): point is { minute: number; probability: number } => point.probability !== null && point.minute >= xMin && point.minute <= xMax)
              .map((point) => `${x(point.minute)},${y(point.probability)}`)
              .join(" ");
            if (!points) return null;
            return (
              <polyline
                key={item.ticker}
                points={points}
                fill="none"
                stroke={PALETTE[index % PALETTE.length]}
                strokeWidth="2.2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            );
          })}
        </svg>
      </div>

      {(hrrrNow || nbmNow) && (
        <div className={reactionStyles.modelContext}>
          <b>Explanatory models at ~{clockLabel(latestMinute)}</b>
          <span>
            HRRR: {hrrrNow?.temp !== null && hrrrNow ? `${hrrrNow.temp.toFixed(1)}°F` : "—"} · cloud {hrrrNow?.cloudCover !== null && hrrrNow ? `${hrrrNow.cloudCover.toFixed(0)}%` : "—"} · solar {hrrrNow?.shortwaveRadiation !== null && hrrrNow ? `${hrrrNow.shortwaveRadiation.toFixed(0)} W/m²` : "—"}
          </span>
          <span>
            NBM: {nbmNow?.temp !== null && nbmNow ? `${nbmNow.temp.toFixed(1)}°F` : "—"} · cloud {nbmNow?.cloudCover !== null && nbmNow ? `${nbmNow.cloudCover.toFixed(0)}%` : "—"}
          </span>
          <small>HRRR/NBM explain possible TWC failure modes only. They never replace the stored TWC market anchor.</small>
        </div>
      )}

      {reactions.length > 0 && (
        <div className={reactionStyles.reactionTable}>
          <div className={reactionStyles.reactionTableHeader}>
            <span>Shock</span><span>+5m center</span><span>+15m center</span><span>Largest bucket move</span>
          </div>
          {reactions.map((reaction) => (
            <div className={reactionStyles.reactionTableRow} key={reaction.shock.time}>
              <span><b>{clockLabel(reaction.shock.minute)}</b><small>{reaction.shock.label}</small></span>
              <span>{reaction.move5 === null ? "—" : `${reaction.move5 >= 0 ? "+" : ""}${reaction.move5.toFixed(2)}°F`}</span>
              <span>{reaction.move15 === null ? "—" : `${reaction.move15 >= 0 ? "+" : ""}${reaction.move15.toFixed(2)}°F`}</span>
              <span>{reaction.strongest ? `${reaction.strongest.label} ${reaction.strongest.delta >= 0 ? "+" : ""}${(reaction.strongest.delta * 100).toFixed(0)}¢` : "—"}</span>
            </div>
          ))}
        </div>
      )}

      <p className={styles.trajectoryNote}>
        Kalshi bucket lines use public one-minute bid/ask midpoints (last trade when a side is missing). “Market center” is a probability-weighted bucket center for visual reaction tracking, not an exact expectation because the edge buckets are open-ended. Vertical markers use the same local clock as the TWC trajectory above. TWC revisions are shown separately from observed weather shocks so their market effects can be tested independently.
      </p>
    </section>
  );
}
