"use client";

import { useMemo, useState } from "react";
import { clearSkyWatts, SOLAR_STATIONS, type SolarHistoryPoint } from "@/lib/weather/solar-model";
import styles from "./SolarHeatingGauge.module.css";

function clock(time: string, zone: string) {
  return new Intl.DateTimeFormat("en-US",{hour:"numeric",minute:"2-digit",timeZone:zone}).format(new Date(time));
}
function sunlightColor(watts: number | null) {
  return watts === null ? "#a6b4bf" : watts < 100 ? "#8895ae" : watts < 300 ? "#67a6b3" : watts < 600 ? "#d4af55" : "#ec9250";
}

export function SunlightTimeline({ stid, points, selectedTime, onSelect, loading }: { stid: string; points: SolarHistoryPoint[]; selectedTime: string | null; onSelect: (time: string | null) => void; loading: boolean }) {
  const [metric,setMetric] = useState<"sunlight" | "clouds">("sunlight");
  const [window,setWindow] = useState<"today" | "recent">("today");
  const station = SOLAR_STATIONS.find(s => s.stid === stid)!;
  const recentStart = points.length ? Date.parse(points.at(-1)!.time) - 3 * 3600000 : 0;
  const pinned = window === "recent" && selectedTime !== null && Date.parse(selectedTime) < recentStart;
  const visible = useMemo(() => window === "today" || !points.length ? points : points.filter(p => Date.parse(p.time) >= Math.min(Date.parse(points.at(-1)!.time) - 3 * 3600000,selectedTime ? Date.parse(selectedTime) : Infinity)),[points,window,selectedTime]);
  const last = visible.at(-1);
  const first = visible[0];
  const start = first ? Date.parse(first.time) - 10 * 60000 : 0;
  const end = last ? Math.max(start + 3600000,Date.parse(last.time) + 10 * 60000) : 1;
  const left = 46, right = 746, top = 20, bottom = 174;
  const maximum = metric === "clouds" ? Math.max(120,Math.ceil(Math.max(...visible.map(p=>p.clearSkyPercent ?? 0)) / 40)*40) : Math.max(1000,Math.ceil(Math.max(...visible.map(p=>p.watts ?? 0)) / 200)*200);
  const x = (time: string | number) => left + ((typeof time === "number" ? time : Date.parse(time)) - start) / (end - start) * (right - left);
  const y = (value: number) => bottom - value / maximum * (bottom - top);
  const measured = (p: SolarHistoryPoint) => metric === "sunlight" ? p.watts : p.clearSkyPercent;
  const focused = visible.find(p=>p.time === selectedTime) ?? last;
  const focusIndex = Math.max(0,visible.findIndex(p=>p.time === focused?.time));
  const reference: string[] = [];
  for (let time = start; time <= end; time += 300000) reference.push(`${x(time)},${y(metric === "clouds" ? 100 : clearSkyWatts(new Date(time),station.lat,station.lon))}`);
  const ticks = [0,maximum / 2,maximum];
  const timeTicks = [start,(start + end)/2,end];
  function scrub(clientX: number, rect: DOMRect) {
    const svgX = (clientX - rect.left) / rect.width * 780;
    const time = start + (svgX - left) / (right - left) * (end - start);
    const point = visible.reduce((best,p)=>Math.abs(Date.parse(p.time) - time) < Math.abs(Date.parse(best.time) - time) ? p : best,visible[0]);
    if (point) onSelect(point.time);
  }

  return (
    <div className={styles.timeline}>
      <div className={styles.timelineHeader}><div><span className={styles.eyebrow}>The sunlight story{first ? ` · ${new Intl.DateTimeFormat("en-US",{month:"short",day:"numeric",timeZone:station.timezone}).format(new Date(first.time))}` : ""}</span><h4>{window === "today" ? "Daylight, scan by scan" : pinned ? "Recent daylight + pinned scan" : "The latest three daylight hours"}</h4></div><div className={styles.switcher} aria-label="Timeline window"><button aria-pressed={window === "today"} onClick={()=>{setWindow("today");onSelect(null);}}>Day</button><button aria-pressed={window === "recent"} onClick={()=>{setWindow("recent");onSelect(null);}}>3h</button></div></div>
      <div className={styles.timelineTools}><div className={styles.metricTabs} aria-label="Timeline metric"><button aria-pressed={metric === "sunlight"} onClick={()=>setMetric("sunlight")}>☀ Sunlight</button><button aria-pressed={metric === "clouds"} onClick={()=>setMetric("clouds")}>☁ Cloud effect</button></div><span>{metric === "sunlight" ? "Energy reaching the surface" : "Sun-angle-adjusted comparison"}</span></div>
      {visible.length > 1 ? <>
        <svg className={styles.chart} viewBox="0 0 780 212" role="img" aria-label={`${metric === "sunlight" ? "Surface sunlight" : "Sunlight relative to clear sky"} through time. Use the slider below to explore actual satellite scans.`} onPointerDown={event=>{event.currentTarget.setPointerCapture(event.pointerId);scrub(event.clientX,event.currentTarget.getBoundingClientRect());}} onPointerMove={event=>{if(event.pointerType === "mouse" || event.buttons) scrub(event.clientX,event.currentTarget.getBoundingClientRect());}}>
          {ticks.map(t=><g key={t}><line x1={left} x2={right} y1={y(t)} y2={y(t)} stroke="#dde5e4" strokeDasharray="3 5" /><text x={left - 8} y={y(t) + 4} textAnchor="end" className={styles.axis}>{Math.round(t)}{metric === "clouds" ? "%" : ""}</text></g>)}
          <polyline points={reference.join(" ")} fill="none" stroke="#a99b76" strokeWidth="2" strokeDasharray="5 6" />
          {visible.slice(1).map((p,index)=>{
            const prior=visible[index], a=measured(prior), b=measured(p);
            if(a === null || b === null || Date.parse(p.time) - Date.parse(prior.time) > 45 * 60000) return null;
            return <g key={p.time}><path d={`M ${x(prior.time)} ${bottom} L ${x(prior.time)} ${y(a)} L ${x(p.time)} ${y(b)} L ${x(p.time)} ${bottom} Z`} fill={sunlightColor(p.watts)} opacity=".10" /><line x1={x(prior.time)} y1={y(a)} x2={x(p.time)} y2={y(b)} stroke={sunlightColor(p.watts)} strokeWidth="3.5" strokeLinecap="round" /></g>;
          })}
          {visible.map(p=>measured(p) === null ? <line key={p.time} x1={x(p.time)} x2={x(p.time)} y1={bottom - 3} y2={bottom + 3} stroke="#7b8790" /> : <circle key={p.time} cx={x(p.time)} cy={y(measured(p)!)} r="3" fill={sunlightColor(p.watts)} />)}
          {focused && measured(focused) !== null && <g><line x1={x(focused.time)} x2={x(focused.time)} y1={top} y2={bottom} stroke="#526d75" strokeDasharray="3 4" /><circle cx={x(focused.time)} cy={y(measured(focused)!)} r="8" fill={sunlightColor(focused.watts)} stroke="white" strokeWidth="3" /></g>}
          {timeTicks.map(t=><text key={t} x={x(t)} y="199" textAnchor="middle" className={styles.axis}>{clock(new Date(t).toISOString(),station.timezone)}</text>)}
        </svg>
        <div className={styles.legend}><span><i className={styles.legendMeasured} />Satellite sunlight</span><span><i className={styles.legendReference} />{metric === "clouds" ? "Clear-sky reference · 100%" : "Approximate clear sky"}</span></div>
        <label className={styles.scrubber}><span>Slide through the day</span><input type="range" min="0" max={visible.length - 1} value={focusIndex} onChange={event=>onSelect(visible[Number(event.target.value)].time)} aria-label="Explore satellite scans by time" aria-valuetext={focused ? `${clock(focused.time,station.timezone)}, ${focused.watts === null ? "missing reading" : `${Math.round(focused.watts)} watts per square meter`}` : undefined} /></label>
        {focused && <div className={styles.scanDetail}><span>{selectedTime ? "Viewing" : "Last daylight scan"} <b>{clock(focused.time,station.timezone)}</b></span><span><b>{focused.watts === null ? "Missing" : Math.round(focused.watts)}</b> W/m²</span><span><b>{focused.clearSkyPercent === null ? "—" : `${Math.round(focused.clearSkyPercent)}%`}</b> of clear sky</span>{selectedTime && <button onClick={()=>onSelect(null)}>Back to now ↗</button>}</div>}
      </> : <div className={styles.historyEmpty}>{loading ? "Loading today’s satellite story. Live readings arrive first; the timeline fills in as older scans are decoded." : "More valid daylight scans are needed to draw the timeline."}</div>}
      <p className={styles.timelineHint}>{metric === "clouds" ? "A rise here means more sunlight is getting through, after accounting for the Sun’s movement. A dip suggests greater suppression." : "Colored line = sunlight reaching the ground. If it dips below the dashed clear-sky curve, sunlight is being suppressed. Late-day declines can also come from the setting Sun."} Tap or drag to replay. Missing readings remain gaps.</p>
    </div>
  );
}
