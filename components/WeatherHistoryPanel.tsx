"use client";

import { useEffect, useMemo, useState } from "react";
import { quoteMid } from "@/lib/weather/market-reaction";
import styles from "@/components/WeatherHistoryPanel.module.css";

type Quote = { time: string; yesBid: number | null; yesAsk: number | null; lastPrice: number | null };
type Bucket = {
  ticker: string;
  label: string;
  lower: number | null;
  upper: number | null;
  quotes: Quote[];
  latestMid: number | null;
};
type MarketPayload = {
  date: string;
  eventTicker: string | null;
  seriesTicker: string;
  markets: Bucket[];
  winningBucket: { ticker: string; label: string; lower: number | null; upper: number | null; settlementValue: number | null } | null;
  error?: string;
};
type HistoryPayload = {
  stid: string;
  city: string;
  date: string;
  baseline: {
    source: "twc";
    forecastHigh: number | null;
    capturedAt: string;
    issuedAt: string | null;
    provenance: string;
  } | null;
  message?: string;
  error?: string;
};

const PALETTE = ["#173f32", "#2e6652", "#648043", "#a87b34", "#a3543f", "#6e5681", "#3f668e", "#815760", "#53777b"];

function localDateLabel(iso: string, timezone: string) {
  try {
    return new Intl.DateTimeFormat("en-CA", {
      year: "numeric", month: "2-digit", day: "2-digit", timeZone: timezone,
    }).format(new Date(iso));
  } catch {
    return "";
  }
}

function minuteOfDay(iso: string, timezone: string) {
  const parts = new Intl.DateTimeFormat("en-US", {
    hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: timezone,
  }).formatToParts(new Date(iso));
  const hour = Number(parts.find((part) => part.type === "hour")?.value ?? "0");
  const minute = Number(parts.find((part) => part.type === "minute")?.value ?? "0");
  return hour * 60 + minute;
}

function defaultDate() {
  return new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
}

function shortCaptured(iso: string, timezone: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: timezone,
  }).format(new Date(iso));
}

export function WeatherHistoryPanel({ stid, city, timezone }: { stid: string; city: string; timezone: string }) {
  const [date, setDate] = useState(defaultDate);
  const [history, setHistory] = useState<HistoryPayload | null>(null);
  const [market, setMarket] = useState<MarketPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      try {
        const qs = `stid=${encodeURIComponent(stid)}&date=${encodeURIComponent(date)}`;
        const [historyResponse, marketResponse] = await Promise.all([
          fetch(`/api/weather-dashboard/history-day?${qs}`, { cache: "no-store" }),
          fetch(`/api/weather-dashboard/market-reaction?${qs}`, { cache: "no-store" }),
        ]);
        const historyJson = await historyResponse.json() as HistoryPayload;
        const marketJson = await marketResponse.json() as MarketPayload;
        if (!historyResponse.ok) throw new Error(historyJson.error ?? "Historical TWC lookup failed");
        if (!marketResponse.ok) throw new Error(marketJson.error ?? "Historical Kalshi lookup failed");
        if (!cancelled) {
          setHistory(historyJson);
          setMarket(marketJson);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Unable to load archived day");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [stid, date]);

  const visibleMarkets = useMemo(() => {
    return (market?.markets ?? [])
      .map((bucket) => ({
        ...bucket,
        quotes: bucket.quotes.filter((quote) => localDateLabel(quote.time, timezone) === date),
      }))
      .filter((bucket) => bucket.quotes.length)
      .sort((a, b) => (a.lower ?? -999) - (b.lower ?? -999));
  }, [market, timezone, date]);

  const width = 760;
  const height = 270;
  const pad = { left: 44, right: 18, top: 18, bottom: 34 };
  const xMin = 6 * 60;
  const xMax = 22 * 60;
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const x = (minute: number) => pad.left + ((minute - xMin) / (xMax - xMin)) * plotW;
  const y = (probability: number) => pad.top + ((1 - probability) * plotH);
  const ticks = [6, 9, 12, 15, 18, 21];
  const yTicks = [0, .25, .5, .75, 1];

  return (
    <section className={styles.panel}>
      <header className={styles.header}>
        <div>
          <span>Frozen day explorer</span>
          <h2>{city} · archived Kalshi + TWC</h2>
          <p>Pick a date and replay that day without mixing in today's prices or forecast.</p>
        </div>
        <label className={styles.dateControl}>
          <span>Date</span>
          <input type="date" value={date} max={new Date().toISOString().slice(0, 10)} onChange={(event) => setDate(event.target.value)} />
        </label>
      </header>

      {error && <div className={styles.error}>{error}</div>}
      {loading && <div className={styles.loading}>Loading frozen day…</div>}

      {!loading && !error && (
        <>
          <div className={styles.statGrid}>
            <div><span>City / station</span><b>{city}</b><small>{stid}</small></div>
            <div><span>Frozen TWC high</span><b>{history?.baseline?.forecastHigh == null ? "—" : `${history.baseline.forecastHigh.toFixed(0)}°F`}</b><small>{history?.baseline ? history.baseline.provenance : "No archived snapshot"}</small></div>
            <div><span>Winning bucket</span><b>{market?.winningBucket?.label ?? "—"}</b><small>{market?.winningBucket?.settlementValue == null ? "Settlement value unavailable" : `Settled ${market.winningBucket.settlementValue}°F`}</small></div>
            <div><span>Kalshi event</span><b>{market?.eventTicker ?? "—"}</b><small>{market?.markets.length ?? 0} buckets archived</small></div>
          </div>

          {history?.baseline && (
            <div className={styles.freezeNote}>
              TWC high frozen from <b>{shortCaptured(history.baseline.capturedAt, timezone)}</b>. The chart below uses only Kalshi quotes whose timestamp belongs to <b>{date}</b> in {city}.
            </div>
          )}

          {visibleMarkets.length ? (
            <>
              <div className={styles.legend}>
                {visibleMarkets.map((bucket, index) => (
                  <span key={bucket.ticker}><i style={{ background: PALETTE[index % PALETTE.length] }} />{bucket.label}</span>
                ))}
              </div>
              <div className={styles.chart}>
                <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${city} Kalshi bucket prices on ${date}`}>
                  {yTicks.map((tick) => (
                    <g key={tick}>
                      <line x1={pad.left} x2={width - pad.right} y1={y(tick)} y2={y(tick)} className={styles.gridLine} />
                      <text x={pad.left - 8} y={y(tick) + 4} textAnchor="end" className={styles.axis}>{Math.round(tick * 100)}%</text>
                    </g>
                  ))}
                  {ticks.map((hour) => (
                    <text key={hour} x={x(hour * 60)} y={height - 10} textAnchor="middle" className={styles.axis}>
                      {hour > 12 ? hour - 12 : hour}{hour >= 12 ? "p" : "a"}
                    </text>
                  ))}
                  {visibleMarkets.map((bucket, index) => {
                    const points = bucket.quotes
                      .map((quote) => ({ minute: minuteOfDay(quote.time, timezone), probability: quoteMid(quote) }))
                      .filter((point): point is { minute: number; probability: number } => point.probability !== null && point.minute >= xMin && point.minute <= xMax)
                      .map((point) => `${x(point.minute)},${y(point.probability)}`)
                      .join(" ");
                    return points ? <polyline key={bucket.ticker} points={points} fill="none" stroke={PALETTE[index % PALETTE.length]} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /> : null;
                  })}
                </svg>
              </div>

              <div className={styles.bucketTable}>
                <div className={styles.bucketHead}><span>Bucket</span><span>Last archived probability</span><span>Settlement</span></div>
                {visibleMarkets.map((bucket) => {
                  const latest = [...bucket.quotes].sort((a, b) => new Date(b.time).getTime() - new Date(a.time).getTime())[0] ?? null;
                  const mid = latest ? quoteMid(latest) : null;
                  const winner = market?.winningBucket?.ticker === bucket.ticker;
                  return (
                    <div className={winner ? styles.winnerRow : styles.bucketRow} key={bucket.ticker}>
                      <span><b>{bucket.label}</b><small>{bucket.ticker}</small></span>
                      <span>{mid === null ? "—" : `${(mid * 100).toFixed(0)}%`}</span>
                      <span>{winner ? "WINNER" : "—"}</span>
                    </div>
                  );
                })}
              </div>
            </>
          ) : <div className={styles.empty}>No archived Kalshi minute tape was returned for {date}.</div>}
        </>
      )}
    </section>
  );
}
