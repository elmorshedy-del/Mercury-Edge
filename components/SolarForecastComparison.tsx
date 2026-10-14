"use client";
import { forecastAt, forecastGap, type SolarForecastPayload } from '@/lib/weather/solar-forecast-model';
import type { SolarHistoryPoint, SolarSample } from '@/lib/weather/solar-model';
import styles from './SolarHeatingGauge.module.css';
const signed=(n:number)=>`${n>=0?'+':''}${Math.round(n)}`;
const stamp=(time:string,zone:string)=>new Intl.DateTimeFormat('en-US',{month:'short',day:'numeric',hour:'numeric',minute:'2-digit',timeZone:zone}).format(new Date(time));

export function SolarForecastComparison({forecast,sample,history,clearSky,zone,stale,error}: {forecast:SolarForecastPayload | null;sample:SolarSample | null;history:SolarHistoryPoint[];clearSky:number;zone:string;stale:boolean;error:boolean}) {
  const gap=sample && !stale ? forecastGap(sample.watts,forecastAt(forecast?.points ?? [],sample.time),clearSky) : null;
  const prior=sample ? [...history].reverse().find(p=>Date.parse(sample.time)-Date.parse(p.time)>=25*60000 && Date.parse(sample.time)-Date.parse(p.time)<=45*60000 && p.watts !== null) : null;
  const priorGap=prior ? forecastGap(prior.watts,forecastAt(forecast?.points ?? [],prior.time),prior.clearSkyWatts) : null;
  const shift=gap && priorGap ? gap.deltaWatts-priorGap.deltaWatts : null;
  const tone=gap?.tone ?? 'waiting';
  const title=stale ? 'A fresh scan is needed' : !gap ? error ? 'Forecast unavailable' : forecast?.loading ? 'Loading the pre-day forecast…' : 'No forecast match for this scan' : tone==='more' ? 'Brighter than HRRR expected' : tone==='less' ? 'Dimmer than HRRR expected' : 'Sunlight close to HRRR forecast';
  const explanation=!gap ? 'The comparison waits for a valid satellite scan and adjacent forecast hours. Live sunlight updates continue independently.'
    : tone==='near' ? 'The sunlight reaching the surface is close to the model’s expected heating supply—even if the sky looks cloudy.'
    : tone==='more' ? 'More solar energy is reaching the surface than this model expected. That adds heating fuel; wind and mixing decide the temperature response.'
    : 'Less solar energy is reaching the surface than this model expected. That reduces heating fuel; it does not by itself imply a temperature reversal.';
  const position=gap ? 50+Math.max(-1,Math.min(1,gap.deltaWatts/Math.max(200,clearSky*.5)))*48 : 50;
  const published=forecast?.points.length ? forecast.points.map(p=>p.publishedAt).sort().at(-1)! : null;
  return <div className={styles.forecastCard} data-surprise={tone}>
    <div className={styles.forecastHeader}><span className={styles.eyebrow}>Actual vs pre-day forecast</span><span className={styles.modelTag}>NOAA HRRR · fixed run</span></div>
    <h4>{title}</h4><p>{explanation}</p>
    <div className={styles.surpriseTrack} aria-hidden="true"><span className={styles.surpriseCenter} />{gap && <i style={{left:`${position}%`}} />}</div>
    <div className={styles.surpriseLabels}><span>Less heating fuel</span><span>Close</span><span>More heating fuel</span></div>
    {gap && <div className={styles.forecastNumbers}><span>Actual <b>{Math.round(sample!.watts!)} W/m²</b></span><span>Forecast ≈<b>{Math.round(gap.expectedWatts)} W/m²</b></span><span>Gap <b>{signed(gap.deltaWatts)} W/m²{gap.percent!==null ? ` (${signed(gap.percent)}%)` : ''}</b></span></div>}
    {shift!==null && <div className={styles.gapShift}>{shift>0?'↗ Gap moved toward brighter':shift<0?'↘ Gap moved toward dimmer':'→ Forecast gap unchanged'} · {signed(shift)} W/m² in {Math.round((Date.parse(sample!.time)-Date.parse(prior!.time))/60000)} min, beyond the forecast’s own change</div>}
    {sample && <div className={styles.forecastProvenance}>Compared scan {stamp(sample.time,zone)} · forecast interpolated to this scan’s midpoint</div>}
    {forecast && <div className={styles.forecastProvenance}>Run initialized {stamp(forecast.runAt,zone)} · {published ? `forecast files published by ${stamp(published,zone)}` : 'checking original publication times'}{forecast.loading?' · curve loading':''}</div>}
    <small className={styles.forecastCaveat}>HRRR sunlight benchmark; the temperature curve uses TWC. “Close” means within a display band, not statistical confidence or proof of market pricing.</small>
    {(error || forecast?.error) && <p className={styles.feedWarning}>{forecast?.error ?? 'Forecast source request failed. Available comparisons retain their original run.'}</p>}
  </div>;
}
