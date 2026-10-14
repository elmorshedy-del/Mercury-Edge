import { execFile } from 'node:child_process';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { promisify } from 'node:util';
import { hasDatabase, query } from '../db';
import { replayDay, SOLAR_STATIONS, type SolarStation } from './solar-model';
import { dswrfRange, eligibleForecast, type SolarForecastPayload, type SolarForecastPoint } from './solar-forecast-model';

const execute = promisify(execFile);
/** ecCodes JSON contains both verified field metadata and the actual nearest model grid point. */
export function parseForecastDecode(output: string, date: string, hour: number) {
  const records=JSON.parse(output);
  if (!Array.isArray(records) || records.length !== 1) throw new Error('Expected one radiation message');
  const {keys,neighbours}=records[0];
  const valid=new Date(Date.parse(`${date}T00:00:00Z`)+hour*3600000);
  const validDate=Number(valid.toISOString().slice(0,10).replaceAll('-',''));
  const p=neighbours?.[0];
  if (!['sdswrf','dswrf'].includes(keys?.shortName) || keys.typeOfLevel !== 'surface' || Number(keys.level) !== 0 || Number(keys.discipline) !== 0 || Number(keys.parameterCategory) !== 4 || Number(keys.parameterNumber) !== 7 || keys?.units !== 'W m**-2' || keys?.stepType !== 'instant'
    || Number(keys.dataDate) !== Number(date.replaceAll('-','')) || Number(keys.dataTime) !== 0
    || Number(keys.validityDate) !== validDate || Number(keys.validityTime) !== valid.getUTCHours()*100 || Number(keys.step) !== hour
    || neighbours?.length !== 1 || !Number.isFinite(p?.value) || p.value < 0 || p.value > 1600
    || !Number.isFinite(p.latitude) || !Number.isFinite(p.longitude) || !Number.isFinite(p.distance) || p.distance > 5) throw new Error('Radiation field metadata or grid mismatch');
  return {watts:p.value as number,gridLat:p.latitude as number,gridLon:((p.longitude+180)%360+360)%360-180};
}
export async function readForecastFile(path: string, date: string, hour: number, stations = SOLAR_STATIONS) {
  const rows: Record<string,ReturnType<typeof parseForecastDecode>>={};
  // Each decoder releases its grid before the next starts; no concurrent native grids.
  for (const station of stations) {
    const {stdout}=await execute(process.env.GRIB_LS_PATH || 'grib_ls',['-j','-F','%.6f','-p','shortName,units,stepType,dataDate,dataTime,validityDate,validityTime,step,typeOfLevel,level,discipline,parameterCategory,parameterNumber','-l',`${station.lat},${station.lon},1`,path],{timeout:20000,maxBuffer:12000});
    rows[station.stid]=parseForecastDecode(stdout,date,hour);
  }
  return rows;
}
export async function fetchForecastHour(date: string, hour: number): Promise<Record<string,SolarForecastPoint>> {
  const sourceUrl=`https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.${date.replaceAll('-','')}/conus/hrrr.t00z.wrfsfcf${String(hour).padStart(2,'0')}.grib2`;
  const index=await fetch(`${sourceUrl}.idx`,{cache:'no-store',signal:AbortSignal.timeout(15000)});
  if (!index.ok) throw new Error(`HRRR inventory HTTP ${index.status}`);
  const range=dswrfRange(await index.text(),hour);
  if (!range) throw new Error('Instantaneous DSWRF absent');
  const response=await fetch(sourceUrl,{headers:{Range:`bytes=${range.start}-${range.end}`},cache:'no-store',signal:AbortSignal.timeout(30000)});
  if (response.status !== 206 || !response.body || response.headers.get('content-range')?.split('/')[0] !== `bytes ${range.start}-${range.end}`) {
    await response.body?.cancel(); throw new Error('HRRR range response mismatch');
  }
  const published=Date.parse(response.headers.get('last-modified') ?? '');
  if (!Number.isFinite(published)) { await response.body.cancel();throw new Error('HRRR publication timestamp missing'); }
  const publishedAt=new Date(published).toISOString();
  const runAt=`${date}T00:00:00.000Z`, time=new Date(Date.parse(runAt)+hour*3600000).toISOString(), capturedAt=new Date().toISOString();
  // Reject a republished post-day object before downloading or decoding it.
  if (!SOLAR_STATIONS.some(s=>eligibleForecast({runAt,time,publishedAt,capturedAt,sourceUrl,watts:0,gridLat:null,gridLon:null},s,date))) {
    await response.body.cancel(); throw new Error('HRRR object was not published before the target day');
  }
  const reader=response.body.getReader(), chunks: Uint8Array[]=[];
  let size=0;
  try {
    while (true) {
      const {done,value}=await reader.read();
      if (done) break;
      size+=value.length;
      if (size > range.end-range.start+1) { await reader.cancel(); throw new Error('HRRR range exceeds bound'); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  if (size !== range.end-range.start+1) throw new Error('HRRR range truncated');
  const dir=await mkdtemp(join(tmpdir(),'mercury-hrrr-'));
  try {
    const path=join(dir,'radiation.grib2');
    await writeFile(path,Buffer.concat(chunks));
    const values=await readForecastFile(path,date,hour);
    return Object.fromEntries(Object.entries(values).map(([stid,value])=>[stid,{...value,runAt,time,publishedAt,capturedAt,sourceUrl}]));
  } finally { await rm(dir,{recursive:true,force:true}); }
}

export async function readForecastArchive(date: string, read: (date:string)=>Promise<Array<{stid:string;point:SolarForecastPoint}>> = async localDate=>(await query<{stid:string;point:SolarForecastPoint}>('SELECT stid,point FROM weather_solar_forecasts WHERE local_date=$1 AND model=$2',[localDate,'HRRR'])).rows) {
  try { return await read(date); } catch { return []; }
}

type ArchiveRow={stid:string;point:SolarForecastPoint};
export async function saveForecastArchive(date:string, rows:ArchiveRow[], write:(date:string,rows:ArchiveRow[])=>Promise<ArchiveRow[]> = async (localDate,points)=>{
  await query(`INSERT INTO weather_solar_forecasts(stid,local_date,model,valid_at,point)
    SELECT item->>'stid',$1::date,$2,(item->'point'->>'time')::timestamptz,item->'point'
    FROM jsonb_array_elements($3::jsonb) item ON CONFLICT(stid,local_date,model,valid_at) DO NOTHING`,[localDate,'HRRR',JSON.stringify(points)]);
  // Canonical stored rows win if another replica captured the same run first.
  return (await query<ArchiveRow>('SELECT stid,point FROM weather_solar_forecasts WHERE local_date=$1 AND model=$2',[localDate,'HRRR'])).rows;
}) {
  try { return await write(date,rows); } catch { return null; }
}

type Run = {needsPersistence:boolean;points:Record<string,Map<string,SolarForecastPoint>>;loading:boolean;error:string | null;attemptedAt:number};
const runs=new Map<string,Run>();
let queue=Promise.resolve();
const HOURS=Array.from({length:26},(_,i)=>i+8); // Brackets every station's daylight, including summer/DST.
export function forecastRunDue(loading:boolean,complete:boolean,needsPersistence:boolean,elapsedMs:number) {
  return !loading && (!complete || needsPersistence) && elapsedMs >= 300000;
}
function startRun(date: string, now: Date) {
  const prior=runs.get(date);
  const complete=prior && SOLAR_STATIONS.every(s=>prior.points[s.stid]?.size === HOURS.length);
  if (prior && !forecastRunDue(prior.loading,Boolean(complete),prior.needsPersistence,+now-prior.attemptedAt)) return;
  const run=prior ?? {points:{},loading:false,error:null,attemptedAt:0,needsPersistence:false};
  run.loading=true;run.error=null;run.attemptedAt=+now;runs.set(date,run);
  // Only one forecast extraction batch at a time, independent of the live satellite feed.
  queue=queue.then(async()=>{
    try {
      if (hasDatabase) {
        const saved=await readForecastArchive(date);
        for (const {stid,point} of saved) {
          const station=SOLAR_STATIONS.find(s=>s.stid===stid);
          if (station && eligibleForecast(point,station,date)) (run.points[stid] ??= new Map()).set(point.time,point);
        }
      }
      const hourNow=(+now-Date.parse(`${date}T00:00:00Z`))/3600000;
      // Match the freshest observed scan first, then fill the full-day curve.
      const hours=[...HOURS].sort((a,b)=>Math.abs(a-hourNow)-Math.abs(b-hourNow));
      for (const hour of hours) {
        const time=new Date(Date.parse(`${date}T00:00:00Z`)+hour*3600000).toISOString();
        if (SOLAR_STATIONS.every(s=>run.points[s.stid]?.has(time))) continue;
        try {
          const rows=await fetchForecastHour(date,hour);
          for (const station of SOLAR_STATIONS) {
            const point=rows[station.stid];
            if (!point || !eligibleForecast(point,station,date)) continue;
            const series=run.points[station.stid] ??= new Map();
            if (series.has(time)) continue;
            series.set(time,point);run.needsPersistence=hasDatabase;
          }
        } catch (error) {
          console.warn('HRRR solar forecast:',error instanceof Error ? error.message : 'Unavailable');
          run.error='Some forecast hours are unavailable. Only matched hours can be compared; gaps are not filled.';
        }
      }
      if (hasDatabase && run.needsPersistence) {
        const rows=Object.entries(run.points).flatMap(([stid,series])=>[...series.values()].map(point=>({stid,point})));
        const saved=await saveForecastArchive(date,rows);
        if (saved) {
          const keys=new Set(saved.map(row=>`${row.stid}/${row.point.time}`));
          run.needsPersistence=rows.some(row=>!keys.has(`${row.stid}/${row.point.time}`));
          for (const {stid,point} of saved) {
            const station=SOLAR_STATIONS.find(s=>s.stid===stid);
            if (station && eligibleForecast(point,station,date)) (run.points[stid] ??= new Map()).set(point.time,point);
          }
        }
        if (!saved || run.needsPersistence) run.error='Forecast storage is unavailable; this session retains the original NOAA forecast and retries storage.';
      }
    } catch {
      run.error='The forecast archive could not be loaded. Live satellite readings continue independently.';
    } finally {
      run.loading=false;
      for (const [key,value] of runs) if (!value.loading && key < date && runs.size > 3) runs.delete(key);
    }
  }).catch(()=>{run.loading=false;run.error='Forecast loading failed.';});
}
export function getSolarForecast(station: SolarStation, now=new Date()): SolarForecastPayload {
  const localDate=replayDay(station,now);
  startRun(localDate,now);
  const run=runs.get(localDate)!;
  return {model:'HRRR',localDate,runAt:`${localDate}T00:00:00.000Z`,points:[...(run.points[station.stid]?.values() ?? [])].sort((a,b)=>Date.parse(a.time)-Date.parse(b.time)),loading:run.loading,error:run.error};
}
