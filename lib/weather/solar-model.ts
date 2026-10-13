export type SolarStation = { stid: string; lat: number; lon: number; timezone: string; satellite: 18 | 19 };
export const SOLAR_STATIONS: SolarStation[] = [
  { stid: "KNYC", lat: 40.7789, lon: -73.9692, timezone: "America/New_York", satellite: 19 },
  { stid: "KPHL", lat: 39.8721, lon: -75.2411, timezone: "America/New_York", satellite: 19 },
  { stid: "KLAX", lat: 33.93806, lon: -118.38889, timezone: "America/Los_Angeles", satellite: 18 },
  { stid: "KDEN", lat: 39.8561, lon: -104.6737, timezone: "America/Denver", satellite: 18 },
  { stid: "KSEA", lat: 47.4447, lon: -122.3136, timezone: "America/Los_Angeles", satellite: 18 },
];
export type SolarSample = { time: string; scanStart: string; scanEnd: string; publishedAt: string; sourceUrl: string; watts: number | null; nearbyWatts: number | null; validPixels: number };
export type SolarReading = { state: "night" | "live" | "stale" | "unavailable"; watts: number | null; clearSkyWatts: number; clearSkyPercent: number | null; strength: string; trend: "rising" | "falling" | "steady" | "unknown"; trendWatts: number | null; trendPoints: number | null; trendMinutes: number | null; daylightMinutes: number; ageMinutes: number | null; sample: SolarSample | null; previous: SolarSample | null };
export type SolarHistoryPoint = SolarSample & { clearSkyWatts: number; clearSkyPercent: number | null };
export function replayDay(station: SolarStation, now: Date): string {
  const hour = Number(new Intl.DateTimeFormat("en-US",{hour:"numeric",hourCycle:"h23",timeZone:station.timezone}).format(now));
  const day = hour < 12 && sunCosine(now,station.lat,station.lon) <= 0 ? new Date(+now - 12 * 3600000) : now;
  return new Intl.DateTimeFormat("en-CA",{year:"numeric",month:"2-digit",day:"2-digit",timeZone:station.timezone}).format(day);
}
export function historyPoints(station: SolarStation, samples: SolarSample[]): SolarHistoryPoint[] {
  return [...new Map(samples.map(s => [s.time,s])).values()].sort((a,b) => Date.parse(a.time) - Date.parse(b.time)).map(s => {
    const clear = clearSkyWatts(new Date(s.time),station.lat,station.lon);
    return {...s,clearSkyWatts:clear,clearSkyPercent:s.watts !== null && clear >= 100 ? s.watts / clear * 100 : null};
  });
}
const rad = Math.PI / 180;

// NOAA's fractional-year solar geometry; coordinates and timestamps are UTC.
export function sunCosine(time: Date, lat: number, lon: number): number {
  const year = time.getUTCFullYear();
  const day = Math.floor((time.getTime() - Date.UTC(year, 0, 1)) / 86400000) + 1;
  const days = (Date.UTC(year + 1, 0, 1) - Date.UTC(year, 0, 1)) / 86400000;
  const hour = time.getUTCHours() + time.getUTCMinutes() / 60 + time.getUTCSeconds() / 3600;
  const gamma = 2 * Math.PI / days * (day - 1 + (hour - 12) / 24);
  const eq = 229.18 * (0.000075 + 0.001868 * Math.cos(gamma) - 0.032077 * Math.sin(gamma) - 0.014615 * Math.cos(2 * gamma) - 0.040849 * Math.sin(2 * gamma));
  const dec = 0.006918 - 0.399912 * Math.cos(gamma) + 0.070257 * Math.sin(gamma) - 0.006758 * Math.cos(2 * gamma) + 0.000907 * Math.sin(2 * gamma) - 0.002697 * Math.cos(3 * gamma) + 0.00148 * Math.sin(3 * gamma);
  const angle = (hour * 60 + eq + 4 * lon) / 4 - 180;
  return Math.sin(lat * rad) * Math.sin(dec) + Math.cos(lat * rad) * Math.cos(dec) * Math.cos(angle * rad);
}

// Haurwitz approximate clear-sky GHI, with NOAA standard refraction correction.
// This is a physical reference, not a climatological percentile or cloud fraction.
export function clearSkyWatts(time: Date, lat: number, lon: number): number {
  const geometric = sunCosine(time, lat, lon);
  if (geometric <= 0) return 0;
  const elevation = Math.asin(Math.min(1, geometric)) / rad;
  const tan = Math.tan(elevation * rad);
  const correction = elevation > 85 ? 0 : elevation > 5
    ? (58.1 / tan - 0.07 / tan ** 3 + 0.000086 / tan ** 5) / 3600
    : (1735 - 518.2 * elevation + 103.4 * elevation ** 2 - 12.79 * elevation ** 3 + 0.711 * elevation ** 4) / 3600;
  const cosine = Math.sin((elevation + correction) * rad);
  return 1098 * cosine * Math.exp(-0.059 / cosine);
}
export function scanTime(stamp: string): string {
  if (!/^\d{14}$/.test(stamp)) throw new Error("Invalid GOES scan timestamp");
  return new Date(Date.UTC(+stamp.slice(0, 4), 0, +stamp.slice(4, 7), +stamp.slice(7, 9), +stamp.slice(9, 11), +stamp.slice(11, 13), +stamp.slice(13) * 100)).toISOString();
}
export function decodePixel(packed: number, quality: number, scale: number, offset: number, fill: number): number | null {
  const watts = packed * scale + offset;
  return quality === 0 && packed !== fill && Number.isFinite(watts) && watts >= 0 && watts <= 1600 ? watts : null;
}
export function scanAngles(lat: number, lon: number, origin: number): { x: number; y: number } {
  const a = 6378137, b = 6356752.31414, h = 35786023 + a;
  const phi = Math.atan(b * b / (a * a) * Math.tan(lat * rad));
  const r = b / Math.sqrt(1 - (1 - b * b / (a * a)) * Math.cos(phi) ** 2);
  const delta = (lon - origin) * rad;
  const sx = h - r * Math.cos(phi) * Math.cos(delta);
  const sy = -r * Math.cos(phi) * Math.sin(delta);
  const sz = r * Math.sin(phi);
  return { x: Math.asin(-sy / Math.hypot(sx, sy, sz)), y: Math.atan(sz / sx) };
}
export function describeSolar(station: SolarStation, now: Date, sample: SolarSample | null, previous: SolarSample | null): SolarReading {
  const currentClear = clearSkyWatts(now, station.lat, station.lon);
  const night = sunCosine(now, station.lat, station.lon) <= 0;
  let daylightMinutes = 0;
  if (!night) {
    while (daylightMinutes < 1440 && sunCosine(new Date(+now + daylightMinutes * 60000), station.lat, station.lon) > 0) daylightMinutes += 5;
  }
  const age = sample ? (+now - Date.parse(sample.time)) / 60000 : null;
  const usable = sample?.watts != null && age !== null && age >= -2;
  const state = night ? "night" : !usable ? "unavailable" : age! > 35 ? "stale" : "live";
  const clear = sample ? clearSkyWatts(new Date(sample.time), station.lat, station.lon) : currentClear;
  const watts = night ? 0 : usable ? sample!.watts : null;
  const percent = !night && usable && clear >= 100 && currentClear >= 100 ? 100 * watts! / clear : null;
  const result: SolarReading = { state, watts, clearSkyWatts: clear, clearSkyPercent: percent, strength: night ? "None" : watts === null ? "Unavailable" : watts < 100 ? "Little" : watts < 300 ? "Modest" : watts < 600 ? "Useful" : "Strong", trend: "unknown", trendWatts: null, trendPoints: null, trendMinutes: null, daylightMinutes, ageMinutes: age, sample: night ? null : sample, previous: night ? null : previous };
  const elapsed = sample && previous ? (Date.parse(sample.time) - Date.parse(previous.time)) / 60000 : 0;
  const oldClear = previous ? clearSkyWatts(new Date(previous.time), station.lat, station.lon) : 0;
  if (state === "live" && percent !== null && previous?.watts != null && oldClear >= 100 && elapsed >= 15 && elapsed <= 45) {
    result.trendPoints = percent - 100 * previous.watts / oldClear;
    result.trendWatts = watts! - previous.watts;
    result.trendMinutes = elapsed;
    // Descriptive bands only; these are not validated temperature forecast triggers.
    result.trend = result.trendPoints >= 15 ? "rising" : result.trendPoints <= -15 ? "falling" : "steady";
  }
  return result;
}
