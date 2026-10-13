import { createWriteStream } from "node:fs";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Readable, Transform } from "node:stream";
import { pipeline } from "node:stream/promises";
import type { Dataset } from "h5wasm";
import { hasDatabase, query } from "../db";
import { decodePixel, describeSolar, historyPoints, replayDay, scanAngles, scanTime, SOLAR_STATIONS, sunCosine, type SolarSample, type SolarStation } from "./solar-model";
export type SolarObject = { key: string; scanStart: string; scanEnd: string; time: string; publishedAt: string };
type PixelReading = { watts: number | null; nearbyWatts: number | null; validPixels: number };
export function replayDue(loading: boolean, sameDay: boolean, elapsedMs: number): boolean {
  return !loading && (!sameDay || elapsedMs >= 300000);
}
function localDay(time: Date, station: SolarStation) {
  return new Intl.DateTimeFormat("en-CA",{year:"numeric",month:"2-digit",day:"2-digit",timeZone:station.timezone}).format(time);
}
export function chooseHistoryObjects(objects: SolarObject[], stations: SolarStation[], now: Date): SolarObject[] {
  const scans = new Map<string,SolarObject>();
  for (const o of [...objects].sort((a,b) => Date.parse(a.time) - Date.parse(b.time))) {
    const time = new Date(o.time);
    if (+time > +now || !stations.some(s => localDay(time,s) === replayDay(s,now) && sunCosine(time,s.lat,s.lon) > 0)) continue;
    scans.set(o.time,o);
  }
  return [...scans.values()].slice(-105);
}
export function parseSolarObjects(xml: string): SolarObject[] {
  return [...xml.matchAll(/<Contents>([\s\S]*?)<\/Contents>/g)].flatMap((match) => {
    const key = match[1].match(/<Key>([^<]+)<\/Key>/)?.[1];
    const publishedAt = match[1].match(/<LastModified>([^<]+)<\/LastModified>/)?.[1];
    const stamp = key?.match(/_s(\d{14})_e(\d{14})_c\d{14}\.nc$/);
    if (!key || !publishedAt || !stamp) return [];
    const scanStart = scanTime(stamp[1]), scanEnd = scanTime(stamp[2]);
    return [{ key, scanStart, scanEnd, time: new Date((Date.parse(scanStart) + Date.parse(scanEnd)) / 2).toISOString(), publishedAt }];
  });
}
export function chooseSolarObjects(objects: SolarObject[]): SolarObject[] {
  const sorted = [...new Map(objects.map(o => [o.time, o])).values()].sort((a,b) => Date.parse(b.time) - Date.parse(a.time));
  const latest = sorted[0];
  if (!latest) return [];
  const previous = sorted.filter(o => {
    const gap = (Date.parse(latest.time) - Date.parse(o.time)) / 60000;
    return gap >= 15 && gap <= 45;
  }).sort((a,b) => Math.abs(Date.parse(latest.time) - Date.parse(a.time) - 1800000) - Math.abs(Date.parse(latest.time) - Date.parse(b.time) - 1800000))[0];
  return previous ? [latest,previous] : [latest];
}
export async function readSolarFile(path: string, stations: SolarStation[]): Promise<Record<string, PixelReading>> {
  const h5 = await import("h5wasm/node");
  await h5.ready;
  const file = new h5.File(path,"r");
  try {
    const dataset = (name: string) => {
      const d = file.get(name);
      if (!(d instanceof h5.Dataset)) throw new Error(`Satellite dataset missing: ${name}`);
      return d;
    };
    const attribute = (d: Dataset, name: string, fallback?: number) => {
      const v = d.attrs[name]?.value;
      const n = typeof v === "number" ? v : v && typeof v === "object" && "length" in v ? Number((v as ArrayLike<unknown>)[0]) : fallback;
      if (n === undefined || !Number.isFinite(n)) throw new Error(`Satellite attribute missing: ${name}`);
      return n;
    };
    const coordinates = (name: string) => {
      const d = dataset(name);
      const factor = attribute(d,"scale_factor",1), offset = attribute(d,"add_offset",0);
      return Array.from(d.value as ArrayLike<number>, n => n * factor + offset);
    };
    const x = coordinates("x"), y = coordinates("y");
    const origin = attribute(dataset("goes_imager_projection"),"longitude_of_projection_origin");
    const dsr = dataset("DSR"), dqf = dataset("DQF");
    const scale = attribute(dsr,"scale_factor"), offset = attribute(dsr,"add_offset"), fill = attribute(dsr,"_FillValue");
    const nearest = (values: number[], target: number) => {
      if (target < Math.min(values[0],values.at(-1)!) || target > Math.max(values[0],values.at(-1)!)) throw new Error("Station outside satellite view");
      return values.reduce((best,n,i) => Math.abs(n - target) < Math.abs(values[best] - target) ? i : best, 0);
    };
    return Object.fromEntries(stations.map(station => {
      const angles = scanAngles(station.lat,station.lon,origin);
      const col = nearest(x,angles.x), row = nearest(y,angles.y);
      const r0 = Math.max(0,row - 1), c0 = Math.max(0,col - 1), r1 = Math.min(y.length,row + 2), c1 = Math.min(x.length,col + 2);
      const values = dsr.slice([[r0,r1],[c0,c1]]) as ArrayLike<number>;
      const quality = dqf.slice([[r0,r1],[c0,c1]]) as ArrayLike<number>;
      const decoded = Array.from(values,(n,i) => decodePixel(n,quality[i],scale,offset,fill));
      const good = decoded.filter((n): n is number => n !== null).sort((a,b) => a - b);
      const midpoint = Math.floor(good.length / 2);
      const median = good.length ? good.length % 2 ? good[midpoint] : (good[midpoint - 1] + good[midpoint]) / 2 : null;
      return [station.stid,{ watts: decoded[(row - r0) * (c1 - c0) + col - c0], nearbyWatts: median, validPixels: good.length }];
    }));
  } finally { file.close(); }
}

const decodedCache = new Map<string,Record<string, PixelReading>>();
const feedCache = new Map<number,{ checkedAt: number; samples: Record<string,SolarSample[]>; error: string | null }>();
const inFlight = new Map<number,Promise<void>>();
const history = new Map<string,Map<string,SolarSample>>();
const replayStatus = new Map<number,{loading:boolean; loaded:number; target:number; error:string | null; key:string; attemptedAt:number}>();

async function remember(samples: Record<string,SolarSample[]>, persist = true) {
  const rows: Array<{stid:string;sample:SolarSample}> = [];
  for (const [stid,points] of Object.entries(samples)) {
    const series = history.get(stid) ?? new Map<string,SolarSample>();
    for (const point of points) { series.set(point.time,point); rows.push({stid,sample:point}); }
    const ordered = [...series.keys()].sort();
    while (series.size > 180) series.delete(ordered.shift()!);
    history.set(stid,series);
  }
  if (hasDatabase && persist && rows.length) {
    try {
      await query(`INSERT INTO weather_solar_samples(stid,sample_at,sample)
        SELECT item->>'stid',(item->'sample'->>'time')::timestamptz,item->'sample'
        FROM jsonb_array_elements($1::jsonb) item ON CONFLICT(stid,sample_at) DO NOTHING`,[JSON.stringify(rows)]);
    } catch { console.warn("Solar archive write unavailable; retaining memory history"); }
  }
}
function startReplay(satellite: number, now: Date) {
  const stations = SOLAR_STATIONS.filter(s => s.satellite === satellite);
  const key = stations.map(s => replayDay(s,now)).join("/");
  const existing = replayStatus.get(satellite);
  if (existing && !replayDue(existing.loading,existing.key === key,+now - existing.attemptedAt)) return;
  const status = {loading:true,loaded:0,target:0,error:null as string | null,key,attemptedAt:+now};
  replayStatus.set(satellite,status);
  void (async () => {
    try {
      if (hasDatabase) {
        try {
          const rows = await query<{stid:string;sample:SolarSample}>("SELECT stid,sample FROM weather_solar_samples WHERE stid = ANY($1::text[]) AND sample_at >= $2::timestamptz ORDER BY sample_at",[stations.map(s=>s.stid),new Date(+now - 86400000).toISOString()]);
          const saved: Record<string,SolarSample[]> = {};
          for (const row of rows.rows) (saved[row.stid] ??= []).push(row.sample);
          await remember(saved,false);
        } catch { console.warn("Solar archive read unavailable; using NOAA history"); }
      }
      const objects: SolarObject[] = [];
      // Hour listings are tiny; bound their concurrency. NetCDF downloads stay sequential.
      for (let hour = 0; hour < 36; hour += 4) {
        objects.push(...(await Promise.all([0,1,2,3].map(n=>listHour(satellite,new Date(+now - (hour+n)*3600000))))).flat());
      }
      const selected = chooseHistoryObjects(objects,stations,now).reverse();
      status.target = selected.length;
      for (const object of selected) {
        // Live arrivals take priority over replay backfill.
        const liveUpdate = inFlight.get(satellite);
        if (liveUpdate) await liveUpdate;
        if (!stations.every(s => history.get(s.stid)?.has(object.time))) {
          const pixels = await downloadAndRead(satellite,object);
          const points = Object.fromEntries(Object.entries(pixels).map(([stid,pixel]) => [stid,[{...object,...pixel,sourceUrl:`https://noaa-goes${satellite}.s3.amazonaws.com/${object.key}`}]]));
          await remember(points);
        }
        status.loaded++;
      }
    } catch {
      status.error = "Some of today's satellite history could not be loaded. Available scans are shown.";
    } finally { status.loading = false; }
  })();
}

async function listHour(satellite: number, time: Date): Promise<SolarObject[]> {
  const day = Math.floor((+time - Date.UTC(time.getUTCFullYear(),0,1)) / 86400000) + 1;
  const prefix = `ABI-L2-DSRF/${time.getUTCFullYear()}/${String(day).padStart(3,"0")}/${String(time.getUTCHours()).padStart(2,"0")}/`;
  const response = await fetch(`https://noaa-goes${satellite}.s3.amazonaws.com/?list-type=2&prefix=${encodeURIComponent(prefix)}&max-keys=1000`, { cache: "no-store", signal: AbortSignal.timeout(12000) });
  if (!response.ok) throw new Error(`Satellite listing HTTP ${response.status}`);
  return parseSolarObjects(await response.text());
}
async function downloadAndRead(satellite: number, object: SolarObject): Promise<Record<string,PixelReading>> {
  const cached = decodedCache.get(object.key);
  if (cached) return cached;
  const dir = await mkdtemp(join(tmpdir(),"mercury-solar-"));
  const path = join(dir,"scan.nc");
  try {
    const response = await fetch(`https://noaa-goes${satellite}.s3.amazonaws.com/${object.key}`, { cache: "no-store", signal: AbortSignal.timeout(45000) });
    if (!response.ok || !response.body) throw new Error(`Satellite file HTTP ${response.status}`);
    let bytes = 0;
    const limit = new Transform({ transform(chunk, _encoding, callback) {
      bytes += chunk.length;
      callback(bytes > 80000000 ? new Error("Satellite file exceeds download limit") : null, chunk);
    } });
    await pipeline(Readable.fromWeb(response.body as Parameters<typeof Readable.fromWeb>[0]),limit,createWriteStream(path));
    // Read only station neighborhoods. Large files stay on disk, not in the JS heap.
    const result = await readSolarFile(path,SOLAR_STATIONS.filter(s => s.satellite === satellite));
    decodedCache.set(object.key,result);
    while (decodedCache.size > 12) decodedCache.delete(decodedCache.keys().next().value!);
    return result;
  } finally { await rm(dir,{recursive:true,force:true}); }
}
async function refresh(satellite: number, now: Date): Promise<void> {
  const prior = feedCache.get(satellite);
  if (prior && +now - prior.checkedAt < 5000) return;
  if (inFlight.has(satellite)) return inFlight.get(satellite);
  const promise = (async () => {
    try {
      const lists = await Promise.all([listHour(satellite,now),listHour(satellite,new Date(+now - 3600000))]);
      const selected = chooseSolarObjects(lists.flat().filter(o => Date.parse(o.scanEnd) <= +now));
      if (!selected.length) throw new Error("No recent satellite scans published");
      const samples: Record<string,SolarSample[]> = {};
      for (const object of selected.slice(0,1)) {
        const pixels = await downloadAndRead(satellite,object);
        for (const [stid,pixel] of Object.entries(pixels)) (samples[stid] ??= []).push({ ...object, ...pixel, sourceUrl: `https://noaa-goes${satellite}.s3.amazonaws.com/${object.key}` });
      }
      feedCache.set(satellite,{checkedAt:+now,samples,error:null});
      void remember(samples);
    } catch (error) {
      console.warn("Solar satellite feed:",error instanceof Error ? error.message : "Unavailable");
      feedCache.set(satellite,{checkedAt:+now,samples:prior?.samples ?? {},error:"Satellite update failed; any retained scan keeps its original timestamp."});
    } finally { inFlight.delete(satellite); }
  })();
  inFlight.set(satellite,promise);
  return promise;
}
export async function getSolarReading(station: SolarStation, now = new Date()) {
  startReplay(station.satellite,now);
  if (sunCosine(now,station.lat,station.lon) > 0) await refresh(station.satellite,now);
  const feed = feedCache.get(station.satellite);
  const latest = feed?.samples[station.stid]?.[0] ?? null;
  const candidates = latest ? [...(history.get(station.stid)?.values() ?? [])].filter(s=> {
    const gap = Date.parse(latest.time) - Date.parse(s.time);
    return gap >= 15 * 60000 && gap <= 45 * 60000;
  }).sort((a,b)=>Math.abs(Date.parse(latest.time) - Date.parse(a.time) - 1800000) - Math.abs(Date.parse(latest.time) - Date.parse(b.time) - 1800000)) : [];
  const historyDate = replayDay(station,now);
  const today = [...(history.get(station.stid)?.values() ?? [])].filter(s=>localDay(new Date(s.time),station) === historyDate);
  const status = replayStatus.get(station.satellite);
  return { stid:station.stid, satellite:station.satellite, timezone:station.timezone, fetchedAt:new Date().toISOString(), sourceError:sunCosine(now,station.lat,station.lon) > 0 ? feed?.error ?? null : null, historyDate, history:historyPoints(station,today), historyStatus:status ? {loading:status.loading,loaded:status.loaded,target:status.target,error:status.error} : null, ...describeSolar(station,now,latest,candidates[0] ?? null) };
}
