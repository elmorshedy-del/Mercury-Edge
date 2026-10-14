import type { SolarStation } from './solar-model';

export type SolarForecastPoint = {
  time: string; watts: number; runAt: string; publishedAt: string; capturedAt: string;
  sourceUrl: string; gridLat: number | null; gridLon: number | null;
};
export type SolarForecastPayload = {
  model: 'HRRR'; localDate: string; runAt: string; points: SolarForecastPoint[];
  loading: boolean; error: string | null;
};
export type SolarForecastGap = {expectedWatts: number; deltaWatts: number; percent: number | null; tone: 'less' | 'near' | 'more'; bandWatts: number};
const radiation = (n: number) => Number.isFinite(n) && n >= 0 && n <= 1600;
export function eligibleForecast(point: SolarForecastPoint, station: SolarStation, localDate: string): boolean {
  const run = Date.parse(`${localDate}T00:00:00.000Z`), published = Date.parse(point.publishedAt);
  if (Date.parse(point.runAt) !== run || !Number.isFinite(published) || published < run) return false;
  const date = new Intl.DateTimeFormat('en-CA',{year:'numeric',month:'2-digit',day:'2-digit',timeZone:station.timezone}).format(new Date(published));
  return date < localDate && Date.parse(point.time) > run && radiation(point.watts);
}
/** Instantaneous hourly values. Linear interpolation is a timing approximation, not finer model output. */
export function forecastAt(points: SolarForecastPoint[], time: string): number | null {
  const at = Date.parse(time);
  if (!Number.isFinite(at)) return null;
  const sorted = points.filter(p=>radiation(p.watts)).sort((a,b)=>Date.parse(a.time)-Date.parse(b.time));
  const exact = sorted.find(p=>Date.parse(p.time) === at);
  if (exact) return exact.watts;
  const before = sorted.findLast(p=>Date.parse(p.time) < at), after = sorted.find(p=>Date.parse(p.time) > at);
  if (!before || !after || before.runAt !== after.runAt || Date.parse(after.time)-Date.parse(before.time) !== 3600000) return null;
  return before.watts + (after.watts-before.watts)*(at-Date.parse(before.time))/3600000;
}
export function forecastGap(observed: number | null, expected: number | null, clearSky: number): SolarForecastGap | null {
  if (observed === null || expected === null || !radiation(observed) || !radiation(expected)) return null;
  const deltaWatts = observed - expected, bandWatts = Math.max(50,0.1*Math.max(0,clearSky));
  return {expectedWatts:expected,deltaWatts,percent:expected >= 100 ? 100*deltaWatts/expected : null,tone:deltaWatts > bandWatts ? 'more' : deltaWatts < -bandWatts ? 'less' : 'near',bandWatts};
}
export function dswrfRange(index: string, hour: number): {start:number;end:number} | null {
  const rows=index.trim().split('\n');
  const n=rows.findIndex(row=>row.includes(`:DSWRF:surface:${hour} hour fcst:`));
  if (n < 0 || n + 1 >= rows.length) return null;
  const start=Number(rows[n].split(':')[1]), end=Number(rows[n+1].split(':')[1])-1;
  return Number.isSafeInteger(start) && start >= 0 && Number.isSafeInteger(end) && end >= start && end-start < 12000000 ? {start,end} : null;
}
