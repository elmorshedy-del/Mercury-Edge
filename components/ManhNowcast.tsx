"use client";

import { useEffect, useState } from "react";
import styles from "@/components/ManhNowcast.module.css";

type Dist = Array<{ value: number; p: number }>;

type NextMetar =
  | { status: "waiting" | "unavailable"; reason: string; dueAt?: number }
  | {
      status: "ok";
      t0: number;
      tau: number;
      dueAt: number;
      lastTemp: number;
      distribution: Dist;
      pSame: number;
      pUp: number;
      pDown: number;
      mode: number;
      pMode: number;
      mean: number;
      drivers: { manhChange: number; blendChange: number; impliedNow: number; leadMinutes: number };
    };

type Band = { ticker: string; label: string; lower: number | null; upper: number | null; model: number; marketMid: number | null };

type Day =
  | { status: "unavailable"; reason: string }
  | {
      status: "ok";
      cdate: string;
      dayStart: number;
      knownMax: number;
      knownMetar: number;
      nbmTxn: number | null;
      nbmSpread: number | null;
      nbmRuntime: number | null;
      hidden: { p0: number; p1: number; p2: number; p3plus: number; pAbove: number };
      finalDistribution: Dist;
      final: { mode: number; pMode: number; mean: number };
      kalshi: { ticker: string; bands: Band[] } | null;
      reports: Array<{ valid: number; temp: number; routine: boolean; max6: number | null; raw?: string }>;
      path: Array<{ time: number; implied: number | null; manh: number | null }>;
    };

type Payload = {
  generatedAt: number;
  tau: number;
  model: { version: number; trainedThrough: string; stations: string[] };
  stations: Array<{ station: string; dataset: string; weight: number; latest: { time: number; temp: number } | null }>;
  airports?: Array<{
    station: string;
    hf: { time: number; temp: number | null } | null;
    metar: { time: number; temp: number } | null;
  }>;
  lastMetar: { valid: number; temp: number; raw?: string; receivedAt: string | null } | null;
  nextMetar: NextMetar;
  day: Day;
  error?: string;
};

const TZ = "America/New_York";
const STATION_NAMES: Record<string, string> = {
  MANH: "Manhattan (roof)",
  BRON: "Bronx",
  QUEE: "Queens",
  BKLN: "Brooklyn",
  MHMHIL: "Murray Hill",
  MHCHEL: "Chelsea",
};
const AIRPORT_NAMES: Record<string, string> = { EWR: "Newark", CDW: "Caldwell", JRB: "Wall St" };

const localHour = (ms: number) =>
  Number(new Intl.DateTimeFormat("en-US", { hour: "numeric", hourCycle: "h23", timeZone: TZ }).format(new Date(ms)));
const clock = (ms: number) => new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", timeZone: TZ }).format(new Date(ms));
const pct = (p: number) => (p >= 0.995 ? ">99%" : p < 0.005 ? "<1%" : `${Math.round(p * 100)}%`);
const signed = (v: number, digits = 1) => `${v > 0 ? "+" : ""}${v.toFixed(digits)}°`;

function Bars({ dist, highlight, label }: { dist: Dist; highlight?: number; label: string }) {
  const shown = dist.filter((item) => item.p >= 0.01);
  const max = Math.max(...shown.map((item) => item.p), 0.01);
  return (
    <div className={styles.bars} role="list" aria-label={label}>
      {shown.map((item) => (
        <div key={item.value} className={styles.barRow} role="listitem">
          <span className={item.value === highlight ? styles.barKeyHi : styles.barKey}>{item.value}°</span>
          <div className={styles.barTrack}>
            <div className={styles.barFill} style={{ width: `${(item.p / max) * 100}%` }} />
          </div>
          <b>{pct(item.p)}</b>
        </div>
      ))}
    </div>
  );
}

function DayChart({ day }: { day: Extract<Day, { status: "ok" }> }) {
  const width = 760;
  const height = 190;
  const pad = { left: 34, right: 12, top: 12, bottom: 24 };
  const x0 = day.dayStart;
  const x1 = day.dayStart + 24 * 3_600_000;
  const temps = [
    ...day.path.flatMap((p) => [p.implied, p.manh]).filter((v): v is number => v !== null),
    ...day.reports.map((r) => r.temp),
  ];
  if (!temps.length) return null;
  const lo = Math.floor(Math.min(...temps)) - 1;
  const hi = Math.ceil(Math.max(...temps)) + 1;
  const x = (t: number) => pad.left + ((t - x0) / (x1 - x0)) * (width - pad.left - pad.right);
  const y = (v: number) => pad.top + ((hi - v) / (hi - lo)) * (height - pad.top - pad.bottom);
  const line = (key: "implied" | "manh") =>
    day.path
      .filter((p) => p[key] !== null)
      .map((p) => `${x(p.time).toFixed(1)},${y(p[key] as number).toFixed(1)}`)
      .join(" ");
  const ticks = Array.from({ length: 9 }, (_, i) => x0 + i * 3 * 3_600_000);
  const yTicks = Array.from({ length: hi - lo + 1 }, (_, i) => lo + i).filter((v) => (hi - lo > 12 ? v % 2 === 0 : true));
  return (
    <div className={styles.chartScroll}>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Today: blend-implied KNYC path, MANH, and KNYC reports">
        {yTicks.map((v) => (
          <g key={v}>
            <line x1={pad.left} x2={width - pad.right} y1={y(v)} y2={y(v)} className={styles.gridLine} />
            <text x={pad.left - 5} y={y(v) + 3} textAnchor="end" className={styles.axis}>{v}</text>
          </g>
        ))}
        <line x1={pad.left} x2={width - pad.right} y1={y(day.knownMax)} y2={y(day.knownMax)} className={styles.knownLine} />
        {ticks.map((t) => (
          <text key={t} x={x(t)} y={height - 7} textAnchor="middle" className={styles.axis}>{clock(t).replace(":00", "")}</text>
        ))}
        <polyline points={line("manh")} className={styles.manhLine} />
        <polyline points={line("implied")} className={styles.impliedLine} />
        {day.reports.map((r) => (
          <circle key={r.valid} cx={x(r.valid)} cy={y(r.temp)} r={r.routine ? 3.4 : 2.6} className={r.routine ? styles.metarDot : styles.speciDot}>
            <title>{`${clock(r.valid)} ${r.temp}°F${r.max6 !== null ? ` · 6h max ${r.max6}°F` : ""}`}</title>
          </circle>
        ))}
      </svg>
      <div className={styles.legend}>
        <span><i className={styles.legendImplied} />Blend-implied KNYC</span>
        <span><i className={styles.legendManh} />MANH raw</span>
        <span><i className={styles.legendDot} />KNYC METAR</span>
        <span><i className={styles.legendKnown} />Published max</span>
      </div>
    </div>
  );
}

export function ManhNowcast({ selectedStid = "KNYC" }: { selectedStid?: string }) {
  const [data, setData] = useState<Payload | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (selectedStid !== "KNYC") return;
    let cancelled = false;
    const load = async () => {
      try {
        const response = await fetch(`/api/weather-dashboard/manh-nowcast?t=${Date.now()}`, { cache: "no-store" });
        const payload = (await response.json()) as Payload;
        if (!response.ok) throw new Error(payload.error ?? "Nowcast unavailable");
        if (!cancelled) {
          setData(payload);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Nowcast unavailable");
      }
    };
    void load();
    const timer = window.setInterval(load, 60_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [selectedStid]);

  if (selectedStid !== "KNYC") return null;
  if (error && !data) return <section className={styles.panel}><div className={styles.empty}>{error}</div></section>;
  if (!data) return <section className={styles.panel}><div className={styles.empty}>Loading MANH → KNYC nowcast…</div></section>;

  const next = data.nextMetar;
  const day = data.day;
  const manh = data.stations.find((s) => s.station === "MANH")?.latest ?? null;

  return (
    <section className={styles.panel}>
      <header className={styles.header}>
        <div>
          <span>Mesonet → Central Park nowcast · model through {data.model.trainedThrough}</span>
          <h2>What KNYC is doing between hourlies</h2>
        </div>
        <small>
          MANH + 5 neighbouring NYS Mesonet sites, plus Newark, Caldwell and Wall St ASOS, calibrated to KNYC&apos;s 5-minute
          ASOS average. Mesonet data as of {clock(data.tau)}
          {error ? ` · refresh failed: ${error}` : ""}
        </small>
      </header>

      <div className={styles.stateGrid}>
        <div>
          <span>Last KNYC METAR</span>
          <b>{data.lastMetar ? `${data.lastMetar.temp}°F` : "—"}</b>
          <small>{data.lastMetar ? clock(data.lastMetar.valid) : "none"}</small>
        </div>
        <div>
          <span>MANH now</span>
          <b>{manh ? `${manh.temp.toFixed(1)}°F` : "—"}</b>
          <small>{next.status === "ok" ? `${signed(next.drivers.manhChange)} since METAR` : manh ? clock(manh.time) : ""}</small>
        </div>
        <div className={styles.accent}>
          <span>Blend-implied KNYC now</span>
          <b>{next.status === "ok" ? `${next.drivers.impliedNow.toFixed(1)}°F` : "—"}</b>
          <small>{next.status === "ok" ? `blend ${signed(next.drivers.blendChange)} since METAR` : "anchored on last METAR"}</small>
        </div>
        <div>
          <span>Next METAR</span>
          <b>{next.dueAt ? clock(next.dueAt) : "—"}</b>
          <small>{next.status === "ok" ? `${next.drivers.leadMinutes} min after this reading` : next.reason}</small>
        </div>
      </div>

      <div className={styles.columns}>
        <div className={styles.block}>
          <h3>Next hourly METAR</h3>
          {next.status === "ok" ? (
            <>
              <div className={styles.chips}>
                <span className={styles.chipDown}>Lower {pct(next.pDown)}</span>
                <span className={styles.chipSame}>Same {next.lastTemp}° {pct(next.pSame)}</span>
                <span className={styles.chipUp}>Higher {pct(next.pUp)}</span>
              </div>
              <Bars dist={next.distribution} highlight={next.lastTemp} label="Next METAR temperature probabilities" />
              <p className={styles.caption}>Most likely {next.mode}°F ({pct(next.pMode)}). Expected {next.mean.toFixed(1)}°F.</p>
            </>
          ) : (
            <p className={styles.caption}>{next.reason}</p>
          )}
        </div>

        <div className={styles.block}>
          <h3>Daily high (CLI)</h3>
          {day.status === "ok" ? (
            <>
              <div className={styles.chips}>
                <span className={styles.chipSame}>Published max {day.knownMax}°</span>
                <span className={day.hidden.pAbove >= 0.5 ? styles.chipUp : styles.chipNeutral}>
                  Already higher {pct(day.hidden.pAbove)}
                </span>
                {day.nbmTxn !== null && <span className={styles.chipNeutral}>NBM {day.nbmTxn}°{day.nbmSpread !== null ? ` ±${day.nbmSpread}` : ""}</span>}
              </div>
              <Bars dist={day.finalDistribution} highlight={day.knownMax} label="Final CLI high probabilities" />
              <p className={styles.caption}>
                Hidden-max odds: +1° {pct(day.hidden.p1)}, +2° {pct(day.hidden.p2)}, +3°+ {pct(day.hidden.p3plus)} above the published max right now.
                Most likely final high {day.final.mode}°F ({pct(day.final.pMode)}).
              </p>
            </>
          ) : (
            <p className={styles.caption}>{day.reason}</p>
          )}
        </div>
      </div>

      {day.status === "ok" && day.kalshi && day.kalshi.bands.length > 0 && (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr><th>{day.kalshi.ticker}</th><th>Model</th><th>Market mid</th><th>Model − market</th></tr>
            </thead>
            <tbody>
              {day.kalshi.bands
                .slice()
                .sort((a, b) => (a.lower ?? -999) - (b.lower ?? -999))
                .map((band) => {
                  const diff = band.marketMid === null ? null : band.model - band.marketMid;
                  return (
                    <tr key={band.ticker}>
                      <td>{band.label}</td>
                      <td>{pct(band.model)}</td>
                      <td>{band.marketMid === null ? "—" : pct(band.marketMid)}</td>
                      <td className={diff === null ? "" : diff > 0.05 ? styles.pos : diff < -0.05 ? styles.neg : ""}>
                        {diff === null ? "—" : `${diff > 0 ? "+" : ""}${Math.round(diff * 100)} pts`}
                      </td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
          {localHour(data.tau) < 13 && (
            <p className={styles.warn}>
              Before ~1 PM the final-high odds lean mostly on the NBM forecast; rain, cloud timing and fronts that the market
              prices from other models are invisible here. Large morning gaps are more likely a model blind spot than an edge.
            </p>
          )}
        </div>
      )}

      {day.status === "ok" && <DayChart day={day} />}

      <div className={styles.stationRow}>
        {data.stations.map((s) => (
          <div key={s.station}>
            <span>{STATION_NAMES[s.station] ?? s.station}</span>
            <b>{s.latest ? `${s.latest.temp.toFixed(1)}°` : "—"}</b>
            <small>weight {Math.round(s.weight * 100)}%</small>
          </div>
        ))}
      </div>

      {data.airports && data.airports.length > 0 && (
        <div className={styles.airportRow}>
          <span className={styles.airportLead}>Airports (5-min feeds arrive ~15–30 min late)</span>
          {data.airports.map((a) => (
            <div key={a.station}>
              <span>{AIRPORT_NAMES[a.station] ?? a.station} · K{a.station}</span>
              <b>{a.hf?.temp != null ? `${a.hf.temp.toFixed(1)}°` : "—"}</b>
              <small>
                {a.hf ? `5-min ${Math.round((data.generatedAt - a.hf.time) / 60_000)} min old` : "5-min feed down"}
                {a.metar ? ` · METAR ${a.metar.temp}° ${clock(a.metar.time)}` : ""}
              </small>
            </div>
          ))}
        </div>
      )}

      <p className={styles.note}>
        Out-of-sample (2025–2026): at the :50 reading the next METAR&apos;s exact value is the top pick 55% of the time (94% within
        ±1°F), and the CLI high beats the highest hourly METAR on 58% of days. Final-high odds before ~1 PM rely mostly on NBM
        and run slightly hot in the 30–50% range; model vs market uses quote midpoints, not fills. Method:
        docs/MANH_KNYC_CALIBRATION.md.
      </p>
    </section>
  );
}
