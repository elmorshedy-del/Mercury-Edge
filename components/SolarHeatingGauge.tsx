"use client";

import { useEffect, useState } from "react";
import { describeSolar, SOLAR_STATIONS, type SolarHistoryPoint, type SolarReading } from "@/lib/weather/solar-model";
import { SunlightTimeline } from "./SunlightTimeline";
import styles from "./SolarHeatingGauge.module.css";

type Payload = SolarReading & { stid: string; satellite: number; timezone: string; sourceError: string | null; history: SolarHistoryPoint[]; historyStatus: {loading:boolean; loaded:number; target:number; error:string | null} | null };

function clock(time: string, zone: string) {
  return new Intl.DateTimeFormat("en-US",{hour:"numeric",minute:"2-digit",timeZone:zone}).format(new Date(time));
}

export function SolarHeatingGauge({ stid, city }: { stid: string; city: string }) {
  const [rawData,setData] = useState<Payload | null>(null);
  const [selectedTime,setSelectedTime] = useState<string | null>(null);
  const [error,setError] = useState(false);
  const [now,setNow] = useState(() => Date.now());
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const response = await fetch(`/api/weather-dashboard/solar?station=${encodeURIComponent(stid)}`,{cache:"no-store",signal:controller.signal});
        if (!response.ok) throw new Error("Solar feed request failed");
        const next = await response.json() as Payload;
        if (!controller.signal.aborted) { setData(next); setError(false); setNow(Date.now()); }
      } catch {
        if (!controller.signal.aborted) { setError(true); setNow(Date.now()); }
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(poll,5000);
      }
    }
    void poll();
    const ageTimer = setInterval(() => setNow(Date.now()),5000);
    return () => { controller.abort(); clearTimeout(timer); clearInterval(ageTimer); };
  },[stid]);

  const history = rawData?.history ?? [];
  const selected = history.find(p => p.time === selectedTime);
  const station = SOLAR_STATIONS.find(s => s.stid === stid)!;
  const earlier = selected ? [...history].reverse().find(p=>Date.parse(p.time) <= Date.parse(selected.time) - 25 * 60000) ?? null : null;
  const data = rawData ? {...rawData,...describeSolar(station,new Date(selected ? selected.time : now),selected ?? rawData.sample,selected ? earlier : rawData.previous)} : null;
  const sample = data?.sample;
  const age = sample ? Math.max(0,(now - Date.parse(sample.time)) / 60000) : null;
  const state = error && !selected ? "unavailable" : !selected && data?.state === "live" && age !== null && age > 35 ? "stale" : data?.state;
  const night = state === "night";
  const live = state === "live";
  const stale = state === "stale";
  const watts = data?.watts ?? null;
  const percent = data?.clearSkyPercent ?? null;
  const rising = live && data?.trend === "rising";
  const falling = live && data?.trend === "falling";
  const label = !data && !error ? "Loading satellite…" : state === "unavailable" ? "Reading unavailable" : night ? "Sun has set" : stale ? "Old satellite reading" : `${data?.strength} heating`;
  const effect = night ? "The Sun is below the horizon. Any further warming needs another source, such as warmer air moving in."
    : !live ? "A current, good-quality satellite reading is needed to assess the solar heating."
    : rising && watts !== null && watts >= 100 ? "More sunlight is getting through. This gives surface heating more room to help temperatures catch up."
    : falling ? "Less sunlight is getting through. That weakens the solar push toward a higher temperature."
    : watts !== null && watts >= 600 ? "Strong sunlight is available to heat the surface. Wind, moisture and mixing still decide how much the air warms."
    : watts !== null && watts >= 300 ? "Useful sunlight is available for surface heating. A temperature rise is possible while this persists."
    : watts !== null && watts >= 100 ? "Some solar heating is available, but the push is modest."
    : "Little solar energy is available now. Even a clear sky gives little heating when the Sun is low.";
  const arcLength = Math.PI * 80;
  const filled = watts === null || (!live && !night) ? 0 : Math.min(1,watts / 1000);
  const tone = night || !live ? "quiet" : rising ? "rising" : falling ? "falling" : "normal";
  const strength = night ? "Night" : !live ? "—" : data?.strength;
  const needleAngle = Math.PI + filled * Math.PI;
  const zones = [{start:0,end:.1,color:"#8995af"},{start:.1,end:.3,color:"#67a6b3"},{start:.3,end:.6,color:"#d4af55"},{start:.6,end:1,color:"#ec9250"}];

  return (
    <section className={styles.panel} data-tone={tone} aria-label={`${city} sunlight gauge`}>
      <div className={styles.header}>
        <div><span className={styles.eyebrow}>Mercury · sunlight intelligence</span><h3>Sunlight & heating</h3></div>
        <span className={styles.badge}>{selected ? `Replay · ${clock(selected.time,data!.timezone)}` : night ? "☾ After sunset" : stale ? "Stale" : live ? `● GOES-${data?.satellite}` : "Waiting"}</span>
      </div>
      <div className={styles.readout}>
        <div className={styles.dial}>
          <svg viewBox="0 0 200 126" role="img" aria-label={live || night ? `${Math.round(watts ?? 0)} watts per square meter, ${label}` : label}>
            <path d="M 20 100 A 80 80 0 0 1 180 100" className={styles.track} />
            {zones.map(z=><path key={z.start} d="M 20 100 A 80 80 0 0 1 180 100" fill="none" stroke={z.color} strokeWidth="12" strokeDasharray={`${(z.end - z.start) * arcLength - 3} ${arcLength}`} strokeDashoffset={-z.start * arcLength} opacity={live ? .85 : .25} />)}
            {live && <circle cx={100 + 80 * Math.cos(needleAngle)} cy={100 + 80 * Math.sin(needleAngle)} r="8" fill="#274e54" stroke="white" strokeWidth="3" />}
            <text x="100" y="75" textAnchor="middle" className={styles.dialNumber}>{strength}</text>
            <text x="100" y="99" textAnchor="middle" className={styles.dialUnit}>{live || night || stale ? `${Math.round(watts ?? 0)} W/m²` : "Satellite estimate"}</text>
            <text x="17" y="122" className={styles.dialTick}>Little</text><text x="181" y="122" textAnchor="end" className={styles.dialTick}>Strong</text>
          </svg>
        </div>
        <div className={styles.explanation}>
          <strong>{label}</strong>
          <p>{effect}</p>
          {live && data?.trend !== "unknown" && <span className={styles.trend}>{rising ? "↗ Getting brighter" : falling ? "↘ Getting dimmer" : "→ Little change in cloud effect"}{data?.trendWatts !== null ? ` · ${data!.trendWatts! >= 0 ? "+" : ""}${Math.round(data!.trendWatts!)} W/m² in ${Math.round(data!.trendMinutes!)} min` : ""}</span>}
        </div>
      </div>
      <div className={styles.bands} aria-label="Heating strength ranges"><span><b>Little</b>Weak solar push</span><span><b>Modest</b>Some warming fuel</span><span><b>Useful</b>Room to catch up</span><span><b>Strong</b>Plenty of heating fuel</span></div>
      <div className={styles.comparison}>
        <div><span>Compared with clear sky at this time</span><b>{live || stale ? percent === null ? "Sun too low for a stable comparison" : `${Math.round(percent)}% of estimated clear sky` : night ? "No daylight" : "Awaiting satellite"}</b></div>
        <div className={styles.ratioTrack} aria-hidden="true"><i style={{width:`${(live || stale) && percent !== null ? Math.min(100,Math.max(0,percent)) : 0}%`}} /></div>
        {live && percent !== null && <small>{percent < 40 ? "Strongly suppressed" : percent < 70 ? "Partly suppressed" : percent < 90 ? "Much of the clear-sky sunlight" : percent <= 110 ? "Near the clear-sky estimate" : "Above the approximate reference"}{data?.trendPoints !== null ? ` · ${data!.trendPoints! >= 0 ? "+" : ""}${Math.round(data!.trendPoints!)} percentage points versus the earlier scan` : ""}</small>}
      </div>
      <SunlightTimeline stid={stid} points={history} selectedTime={selectedTime} onSelect={setSelectedTime} loading={rawData?.historyStatus?.loading ?? true} />
      <div className={styles.meta}>
        <span>{selected ? "Historical satellite scan · replay mode" : night ? "Sun position calculated for this city" : sample ? `Scan ${clock(sample.scanStart,data!.timezone)}–${clock(sample.scanEnd,data!.timezone)} · ${Math.round(age ?? 0)} min old` : "Waiting for a valid satellite pixel"}</span>
        {live && <span>About {Math.round(data!.daylightMinutes / 5) * 5} min of solar daylight left</span>}
      </div>
      <div className={styles.delivery}><span className={styles.deliveryDot} /><span>New releases checked every 5s · NOAA scan cadence ≈10 min{sample ? ` · published ${clock(sample.publishedAt,data!.timezone)}` : ""}</span>{rawData?.historyStatus?.loading && <span>Replay loading {rawData.historyStatus.loaded}/{rawData.historyStatus.target || "…"}</span>}</div>
      {data?.sourceError && !night && <p className={styles.feedWarning}>{data.sourceError}</p>}
      {rawData?.historyStatus?.error && <p className={styles.feedWarning}>{rawData.historyStatus.error}</p>}
      <details className={styles.details}>
        <summary>How to read this · source & updates</summary>
        <p><b>Read the heating level first.</b> A rising clear-sky percentage means sunlight is recovering beyond the normal change in Sun angle. Combine that with “Useful” or “Strong” heating to spot a possible catch-up window. Persistence matters; the temperature may respond later.</p>
        <p><b>100% is a clear-sky reference, not a weather average.</b> The Haurwitz estimate uses this location and the scan’s Sun angle. Aerosols, humidity, elevation, cloud-edge brightening and satellite error can shift it; readings above 100% are possible. This does not say whether today is statistically unusual. Near sunrise or sunset, the percentage is hidden.</p>
        <p><b>NOAA GOES downwelling shortwave radiation (DSRF)</b> estimates direct plus diffuse sunlight reaching a horizontal surface. It is an estimate over a satellite pixel around the station, not a sunlight sensor at the thermometer. Only good-quality pixels are accepted. New full-disk scans normally arrive every 10 minutes, often about 10–15 minutes behind the scan midpoint. This card requests updates every 5 seconds; shared NOAA listing checks use the same 5-second interval. New files are decoded immediately after discovery, with live requests prioritized over history. Publication time is separate from the scan time. After 35 minutes it marks a reading stale. Today’s replay retains every available daylight scan.</p>
        <p>The strength bands (Little &lt;100, Modest 100–300, Useful 300–600, Strong ≥600 W/m²) and ±15 percentage-point trend threshold are descriptive reading aids. They are not backtested temperature alerts. This gauge does not forecast how many degrees the daily maximum will gain.</p>
        {sample && <p>Station pixel: {sample.watts === null ? "unavailable" : `${Math.round(sample.watts)} W/m²`}; nearby 3×3 median: {sample.nearbyWatts === null ? "unavailable" : `${Math.round(sample.nearbyWatts)} W/m²`} ({sample.validPixels}/9 valid pixels). Published {clock(sample.publishedAt,data!.timezone)}.</p>}
        <div className={styles.links}><a href={`https://www.star.nesdis.noaa.gov/GOES/conus.php?sat=G${data?.satellite ?? (stid === "KNYC" || stid === "KPHL" ? 19 : 18)}`} target="_blank" rel="noreferrer">NOAA satellite loop ↗</a>{sample && <a href={sample.sourceUrl} target="_blank" rel="noreferrer">Original radiation data ↗</a>}<a href="https://pvlib-python.readthedocs.io/en/stable/reference/generated/pvlib.clearsky.haurwitz.html" target="_blank" rel="noreferrer">Clear-sky reference ↗</a></div>
      </details>
    </section>
  );
}
