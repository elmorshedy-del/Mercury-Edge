"use client";

import { useMemo, useState, useSyncExternalStore } from "react";
import { clearSkyWatts, SOLAR_STATIONS, sunCosine, type SolarHistoryPoint } from "@/lib/weather/solar-model";
import { forecastAt, forecastGap, type SolarForecastPayload } from "@/lib/weather/solar-forecast-model";
import styles from "./SolarHeatingGauge.module.css";
function clock(time: string, zone: string) { return new Intl.DateTimeFormat("en-US",{hour:"numeric",minute:"2-digit",timeZone:zone}).format(new Date(time)); }
function sunlightColor(watts: number | null) { return watts === null ? "#a6b4bf" : watts < 100 ? "#8895ae" : watts < 300 ? "#67a6b3" : watts < 600 ? "#d4af55" : "#ec9250"; }
function subscribeViewport(callback: () => void) { const media = window.matchMedia("(max-width: 650px)");media.addEventListener("change",callback);return () => media.removeEventListener("change",callback); }
function narrowViewport() { return window.matchMedia("(max-width: 650px)").matches; }
function serverViewport() { return false; }

export function SunlightTimeline({ stid, points, forecast, selectedTime, onSelect, loading }: { stid: string; points: SolarHistoryPoint[]; forecast: SolarForecastPayload | null; selectedTime: string | null; onSelect: (time: string | null) => void; loading: boolean }) {
  const [metric,setMetric] = useState<"sunlight" | "clouds" | "gap">("sunlight");
  const [window,setWindow] = useState<"today" | "recent">("today");
  const [showClear,setShowClear] = useState(false);
  const narrow = useSyncExternalStore(subscribeViewport,narrowViewport,serverViewport);
  const station = SOLAR_STATIONS.find(s => s.stid === stid)!;
  const recentStart = points.length ? Date.parse(points.at(-1)!.time) - 3 * 3600000 : 0;
  const pinned = window === "recent" && selectedTime !== null && Date.parse(selectedTime) < recentStart;
  const visible = useMemo(() => window === "today" || !points.length ? points : points.filter(p => Date.parse(p.time) >= Math.min(Date.parse(points.at(-1)!.time) - 3 * 3600000,selectedTime ? Date.parse(selectedTime) : Infinity)),[points,window,selectedTime]);
  const last = visible.at(-1), first = visible[0];
  const localDay=(t:string)=>new Intl.DateTimeFormat('en-CA',{year:'numeric',month:'2-digit',day:'2-digit',timeZone:station.timezone}).format(new Date(t));
  const forecastPoints=forecast && (!first || localDay(first.time)===forecast.localDate) ? forecast.points : [];
  const daylight=forecastPoints.filter(p=>localDay(p.time)===forecast?.localDate && sunCosine(new Date(p.time),station.lat,station.lon)>0);
  const firstForecast=daylight.length ? Date.parse(daylight[0].time)-3600000 : Infinity;
  const lastForecast=daylight.length ? Date.parse(daylight.at(-1)!.time)+3600000 : -Infinity;
  const start=first ? window==='today' ? Math.min(Date.parse(first.time)-600000,firstForecast) : Date.parse(first.time)-600000 : Number.isFinite(firstForecast) ? firstForecast : 0;
  const end=Math.max(start+3600000,last ? Date.parse(last.time)+600000 : start+3600000,window==='today'?lastForecast:-Infinity);
  const chartWidth = narrow ? 390 : 780;
  const left = narrow ? 44 : 50, right = chartWidth - 34, top = 20, bottom = 174;
  const matched=(p:SolarHistoryPoint)=>forecastGap(p.watts,forecastAt(forecastPoints,p.time),p.clearSkyWatts);
  const measured=(p:SolarHistoryPoint)=>metric==='sunlight' ? p.watts : metric==='clouds' ? p.clearSkyPercent : matched(p)?.deltaWatts ?? null;
  const maxActual=Math.max(0,...visible.map(p=>p.watts ?? 0),...forecastPoints.map(p=>p.watts));
  const maximum=metric==='gap' ? Math.max(200,Math.ceil(Math.max(0,...visible.map(p=>Math.abs(measured(p) ?? 0)))/100)*100) : metric==='clouds' ? Math.max(120,Math.ceil(Math.max(0,...visible.map(p=>p.clearSkyPercent ?? 0))/40)*40) : Math.max(1000,Math.ceil(maxActual/200)*200);
  const minimum=metric==='gap' ? -maximum : 0;
  const x=(time:string | number)=>left+((typeof time==='number'?time:Date.parse(time))-start)/(end-start)*(right-left);
  const y=(value:number)=>bottom-(value-minimum)/(maximum-minimum)*(bottom-top);
  const color=(p:SolarHistoryPoint)=>metric!=='gap' ? sunlightColor(p.watts) : matched(p)?.tone==='less' ? '#548bab' : matched(p)?.tone==='more' ? '#d88242' : '#85988f';
  const focused=visible.find(p=>p.time===selectedTime) ?? last;
  const focusIndex=Math.max(0,visible.findIndex(p=>p.time===focused?.time));
  const focusGap=focused ? matched(focused) : null;
  const reference: string[]=[];
  for(let time=start;time<=end;time+=300000) reference.push(`${x(time)},${y(metric==='clouds'?100:clearSkyWatts(new Date(time),station.lat,station.lon))}`);
  const forecastSegments: string[][]=[];
  let segment:string[]=[];
  for(let time=start;time<=end;time+=300000) {
    const value=forecastAt(forecastPoints,new Date(time).toISOString());
    if(value===null) {if(segment.length) forecastSegments.push(segment);segment=[];}
    else segment.push(`${x(time)},${y(value)}`);
  }
  if(segment.length) forecastSegments.push(segment);
  const ticks=metric==='gap' ? [-maximum,0,maximum] : [0,maximum/2,maximum];
  const timeTicks=[start,(start+end)/2,end];
  function scrub(clientX:number,rect:DOMRect) {
    const svgX=(clientX-rect.left)/rect.width*chartWidth, time=start+(svgX-left)/(right-left)*(end-start);
    const point=visible.reduce((best,p)=>Math.abs(Date.parse(p.time)-time)<Math.abs(Date.parse(best.time)-time)?p:best,visible[0]);
    if(point) onSelect(point.time);
  }
  return <div className={styles.timeline}>
    <div className={styles.timelineHeader}><div><span className={styles.eyebrow}>The sunlight story{first ? ` · ${new Intl.DateTimeFormat("en-US",{month:"short",day:"numeric",timeZone:station.timezone}).format(new Date(first.time))}` : ""}</span><h4>{metric==='gap' ? 'Where reality departed from forecast' : window==='today' ? 'Expected daylight vs reality' : pinned ? 'Recent daylight + pinned scan' : 'The latest three daylight hours'}</h4></div><div className={styles.switcher} aria-label="Timeline window"><button aria-pressed={window==='today'} onClick={()=>{setWindow('today');onSelect(null);}}>Day</button><button aria-pressed={window==='recent'} onClick={()=>{setWindow('recent');onSelect(null);}}>3h</button></div></div>
    <div className={styles.timelineTools}><div className={styles.metricTabs} aria-label="Timeline metric"><button aria-pressed={metric==='sunlight'} onClick={()=>setMetric('sunlight')}>☀ Sunlight</button><button aria-pressed={metric==='gap'} onClick={()=>setMetric('gap')}>↔ Forecast gap</button><button aria-pressed={metric==='clouds'} onClick={()=>setMetric('clouds')}>☁ Cloud effect</button></div>{metric==='sunlight' && <label className={styles.clearToggle}><input type="checkbox" checked={showClear} onChange={e=>setShowClear(e.target.checked)} /> Clear-sky reference</label>}</div>
    {visible.length>1 || daylight.length>1 ? <>
      <svg className={styles.chart} viewBox={`0 0 ${chartWidth} 212`} role="img" aria-label={`${metric==='gap'?'Actual sunlight minus the frozen HRRR forecast':metric==='sunlight'?'Satellite sunlight and frozen HRRR forecast':'Sunlight relative to clear sky'} through time. Forecasts are hourly, interpolated; use the slider to explore observed scans.`} onPointerDown={event=>{event.currentTarget.setPointerCapture(event.pointerId);scrub(event.clientX,event.currentTarget.getBoundingClientRect());}} onPointerMove={event=>{if(event.pointerType==='mouse'||event.buttons) scrub(event.clientX,event.currentTarget.getBoundingClientRect());}}>
        {ticks.map(t=><g key={t}><line x1={left} x2={right} y1={y(t)} y2={y(t)} stroke={metric==='gap' && t===0?'#718b82':'#dde5e4'} strokeDasharray="3 5" /><text x={left-8} y={y(t)+4} textAnchor="end" className={styles.axis}>{metric==='gap' && t>0?'+':''}{Math.round(t)}{metric==='clouds'?'%':''}</text></g>)}
        {(metric==='clouds'||(metric==='sunlight'&&showClear)) && <polyline points={reference.join(' ')} fill="none" stroke="#a99b76" strokeWidth="1.6" strokeDasharray="2 6" />}
        {metric==='sunlight' && forecastSegments.map((s,i)=><polyline key={i} points={s.join(' ')} fill="none" stroke="#6654a2" strokeWidth="2.5" strokeDasharray="7 5" />)}
        {visible.slice(1).map((p,index)=>{
          const prior=visible[index],a=measured(prior),b=measured(p);
          if(a===null||b===null||Date.parse(p.time)-Date.parse(prior.time)>45*60000) return null;
          const base=metric==='gap'?y(0):bottom;
          return <g key={p.time}><path d={`M ${x(prior.time)} ${base} L ${x(prior.time)} ${y(a)} L ${x(p.time)} ${y(b)} L ${x(p.time)} ${base} Z`} fill={color(p)} opacity=".12" /><line x1={x(prior.time)} y1={y(a)} x2={x(p.time)} y2={y(b)} stroke={color(p)} strokeWidth="3.5" strokeLinecap="round" /></g>;
        })}
        {visible.map(p=>measured(p)===null ? <line key={p.time} x1={x(p.time)} x2={x(p.time)} y1={bottom-3} y2={bottom+3} stroke="#7b8790" /> : <circle key={p.time} cx={x(p.time)} cy={y(measured(p)!)} r="3" fill={color(p)} />)}
        {last && window==='today' && x(last.time)<right-60 && <g><line x1={x(last.time)} x2={x(last.time)} y1={top} y2={bottom} stroke="#afbbb6" strokeDasharray="2 6" /><text x={Math.min(right-50,x(last.time)+8)} y={top+8} className={styles.axis}>Forecast ahead</text></g>}
        {focused && measured(focused)!==null && <g><line x1={x(focused.time)} x2={x(focused.time)} y1={top} y2={bottom} stroke="#526d75" strokeDasharray="3 4" /><circle cx={x(focused.time)} cy={y(measured(focused)!)} r="8" fill={color(focused)} stroke="white" strokeWidth="3" /></g>}
        {timeTicks.map(t=><text key={t} x={x(t)} y="199" textAnchor="middle" className={styles.axis}>{clock(new Date(t).toISOString(),station.timezone)}</text>)}
      </svg>
      <div className={styles.legend}>{metric==='gap' ? <><span><i style={{background:'#548bab',width:16,height:3}} />Below forecast</span><span><i style={{background:'#d88242',width:16,height:3}} />Above forecast</span></> : <><span><i className={styles.legendMeasured} />Satellite sunlight</span>{metric==='sunlight' && <span><i className={styles.legendForecast} />HRRR pre-day forecast</span>}{(metric==='clouds'||showClear) && <span><i className={styles.legendReference} />{metric==='clouds'?'Clear sky · 100%':'Approximate clear sky'}</span>}</>}</div>
      {visible.length>0 && <label className={styles.scrubber}><span>Slide through the day</span><input type="range" min="0" max={visible.length-1} value={focusIndex} onChange={event=>onSelect(visible[Number(event.target.value)].time)} aria-label="Explore satellite scans by time" aria-valuetext={focused ? `${clock(focused.time,station.timezone)}, ${focused.watts===null?'missing reading':`${Math.round(focused.watts)} watts per square meter`}` : undefined} /></label>}
      {focused && <div className={styles.scanDetail}><span>{selectedTime?'Viewing':'Last daylight scan'} <b>{clock(focused.time,station.timezone)}</b></span><span>Actual <b>{focused.watts===null?'Missing':Math.round(focused.watts)}</b> W/m²</span>{metric==='clouds' ? <span><b>{focused.clearSkyPercent===null?'—':`${Math.round(focused.clearSkyPercent)}%`}</b> of clear sky</span> : <><span>HRRR ≈<b>{focusGap?Math.round(focusGap.expectedWatts):'—'}</b> W/m²</span><span>Gap <b>{focusGap?`${focusGap.deltaWatts>=0?'+':''}${Math.round(focusGap.deltaWatts)}`:'—'}</b> W/m²</span></>}{selectedTime && <button onClick={()=>onSelect(null)}>Back to now ↗</button>}</div>}
    </> : <div className={styles.historyEmpty}>{loading?'Loading today’s satellite story. Live readings arrive first; the timeline fills in as older scans are decoded.':'More daylight readings are needed to draw the timeline.'}</div>}
    <p className={styles.timelineHint}>{metric==='gap'?'Zero = the model’s expected sunlight. Blue is less heating fuel than forecast; orange is more. Repeated same-side scans show persistence of the model residual, not a proven temperature trend.':metric==='clouds'?'This removes the approximate change in Sun angle. It is relative to clear sky, not the weather forecast.':'Colored line = satellite estimate. Purple dashes = the previous-evening HRRR weather forecast, including expected cloud suppression. The purple curve beyond the last scan is forecast only. Hourly forecast points are interpolated; rapid cloud timing within an hour is unresolved.'} Tap or drag to replay. Missing readings and unmatched forecast hours remain gaps.</p>
  </div>;
}
