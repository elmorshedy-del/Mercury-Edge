// Live MANH -> KNYC nowcast. Feature definitions mirror research/manh_knyc/build.py exactly;
// tests/manh-nowcast.test.ts replays a historical day through both implementations.
//
// Two questions are answered from NYS Mesonet 5-minute data while KNYC is "dark" between
// its :51 hourly METARs:
//   1. What whole-degree temperature will the next routine KNYC METAR show?
//   2. Has KNYC already exceeded the highest value the public has seen today, and where will
//      the NWS Daily Climate Report (CLI) high finish?

import { featureVector, predictProba, type ExportedBooster } from "./gbm";

export const MINUTE = 60_000;
export const STEP_MS = 5 * MINUTE;
const HOUR = 60 * MINUTE;
const LAT = 40.7789;
const LON = -73.9692;

export type NowcastModel = {
  version: number;
  trained_through: string;
  stations: string[];
  station_datasets: Record<string, string>;
  blend_weights: Record<string, number>;
  publish_lag_min: number;
  nbm_lag_min: number;
  next_metar: ExportedBooster;
  hidden_max: ExportedBooster & { platt_ge1?: [number, number] | null };
  final_high: ExportedBooster & { anchor: string };
  airports?: { stations: string[]; hf_latency_min: number; metar_lag_min: number };
  kalshi_index?: { city: string; stations: string[]; latency_min: number; config_prefix: string };
};

/** Kalshi live weather index feed (GET /live_data/weather/{city}). */
export type KalshiIndexFeed = {
  configVersion: string;
  points: Array<{ t: number; v: number; contributors: number; status: string }>;
};

/** Airport 5-minute HF report (api.weather.gov / MADIS): whole degrees C converted to F. */
export type AirportHfObservation = {
  station: string;
  time: number;
  tempF: number | null;
  dirDeg: number | null;
  speedKt: number | null;
};

/** Airport routine/special METAR with its T-group whole-F temperature. */
export type AirportMetar = { station: string; valid: number; temp: number };

/** One raw NYS Mesonet / Micronet observation (temperatures already in Fahrenheit). */
export type MesonetObservation = {
  time: number; // epoch ms on the 5-minute grid
  station: string;
  tair: number | null;
  srad?: number | null;
  relh?: number | null;
  wspd?: number | null;
  wdir?: number | null;
  pres?: number | null;
  precip?: number | null; // NYSM running accumulation (mm)
};

export type KnycReport = {
  valid: number; // epoch ms of the observation time
  temp: number; // whole-degree F (from the T-group when present)
  max6: number | null; // 6-hour maximum group (whole F) when present
  routine: boolean; // :51 routine METAR with a T-group
  raw?: string;
};

export type NbmRun = { runtime: number; cdate: string; txn: number; xnd: number | null };


export type Grid = {
  start: number;
  length: number;
  temps: Record<string, Float64Array>; // NaN = missing, interpolated like pandas (limit 2, inside)
  srad: Float64Array;
  relh: Float64Array;
  wspd: Float64Array;
  wdir: Float64Array;
  pres: Float64Array;
  precipInc: Float64Array;
};

const nan = Number.NaN;
const isNum = (value: number) => Number.isFinite(value);
const mod = (a: number, n: number) => ((a % n) + n) % n;

// ------------------------------------------------------------------------------------------
// Grid construction
// ------------------------------------------------------------------------------------------

/** pandas Series.interpolate(limit=2, limit_area="inside") for an evenly spaced series. */
export function interpolateInside(values: ArrayLike<number>, limit = 2): Float64Array {
  const out = Float64Array.from(values);
  let index = 0;
  while (index < out.length) {
    if (isNum(out[index])) {
      index += 1;
      continue;
    }
    const runStart = index;
    while (index < out.length && !isNum(out[index])) index += 1;
    const left = runStart - 1;
    const right = index;
    if (left < 0 || right >= out.length) continue;
    for (let fill = runStart; fill < Math.min(runStart + limit, right); fill += 1) {
      out[fill] = out[left] + ((out[right] - out[left]) * (fill - left)) / (right - left);
    }
  }
  return out;
}

export function buildGrid(observations: MesonetObservation[], stations: string[], start: number, end: number): Grid {
  const length = Math.floor((end - start) / STEP_MS) + 1;
  const blank = () => new Float64Array(length).fill(nan);
  const temps: Record<string, Float64Array> = Object.fromEntries(stations.map((station) => [station, blank()]));
  const raw = { srad: blank(), relh: blank(), wspd: blank(), wdir: blank(), pres: blank(), precip: blank() };
  for (const obs of observations) {
    const slot = (obs.time - start) / STEP_MS;
    if (!Number.isInteger(slot) || slot < 0 || slot >= length) continue;
    const series = temps[obs.station];
    if (!series) continue;
    if (typeof obs.tair === "number" && obs.tair >= -40 && obs.tair <= 120) series[slot] = obs.tair;
    if (obs.station === "MANH") {
      for (const key of ["srad", "relh", "wspd", "wdir", "pres", "precip"] as const) {
        const value = obs[key];
        if (typeof value === "number" && Number.isFinite(value)) raw[key][slot] = value;
      }
    }
  }
  const precipInc = blank();
  for (let i = 1; i < length; i += 1) {
    const diff = raw.precip[i] - raw.precip[i - 1];
    if (isNum(diff)) precipInc[i] = Math.max(diff, 0) < 50 ? Math.max(diff, 0) : nan;
  }
  return {
    start,
    length,
    temps: Object.fromEntries(stations.map((station) => [station, interpolateInside(temps[station])])),
    srad: interpolateInside(raw.srad),
    relh: interpolateInside(raw.relh),
    wspd: interpolateInside(raw.wspd),
    wdir: raw.wdir,
    pres: interpolateInside(raw.pres),
    precipInc,
  };
}

function at(series: Float64Array, grid: Grid, time: number): number {
  const slot = (time - grid.start) / STEP_MS;
  if (!Number.isInteger(slot) || slot < 0 || slot >= grid.length) return nan;
  return series[slot];
}

function rollingMean(series: Float64Array, grid: Grid, time: number, window: number, minPeriods: number) {
  let sum = 0;
  let count = 0;
  for (let k = 0; k < window; k += 1) {
    const value = at(series, grid, time - k * STEP_MS);
    if (isNum(value)) {
      sum += value;
      count += 1;
    }
  }
  return count >= minPeriods ? sum / count : nan;
}

function rollingSumFilled(series: Float64Array, grid: Grid, time: number, window: number) {
  let sum = 0;
  let seen = 0;
  for (let k = 0; k < window; k += 1) {
    const slot = (time - k * STEP_MS - grid.start) / STEP_MS;
    if (slot < 0 || slot >= grid.length) continue;
    seen += 1;
    const value = series[slot];
    if (isNum(value)) sum += value;
  }
  return seen ? sum : nan;
}

/** Weighted station change (b - a), re-normalised over stations reporting at both times. */
export function blendChange(model: NowcastModel, a: number[], b: number[], weights = model.blend_weights) {
  let num = 0;
  let wsum = 0;
  let total = 0;
  model.stations.forEach((station, index) => {
    const w = weights[station] ?? 0;
    total += w;
    const diff = b[index] - a[index];
    if (isNum(diff)) {
      num += diff * w;
      wsum += w;
    }
  });
  if (wsum < 0.5 * total || wsum === 0) return nan;
  return (num / wsum) * total;
}

function stationVector(model: NowcastModel, grid: Grid, time: number) {
  return model.stations.map((station) => at(grid.temps[station], grid, time));
}

// ------------------------------------------------------------------------------------------
// Time features
// ------------------------------------------------------------------------------------------

const localFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

function localParts(time: number) {
  const parts = Object.fromEntries(localFormatter.formatToParts(new Date(time)).map((part) => [part.type, part.value]));
  return { year: Number(parts.year), month: Number(parts.month), day: Number(parts.day), hour: Number(parts.hour), minute: Number(parts.minute) };
}

export function solarElevation(time: number) {
  const n = time / 86_400_000 + 2440587.5 - 2451545.0;
  const rad = Math.PI / 180;
  const L = mod(280.46 + 0.9856474 * n, 360) * rad;
  const g = mod(357.528 + 0.9856003 * n, 360) * rad;
  const lam = L + 1.915 * rad * Math.sin(g) + 0.02 * rad * Math.sin(2 * g);
  const eps = (23.439 - 0.0000004 * n) * rad;
  const dec = Math.asin(Math.sin(eps) * Math.sin(lam));
  const ra = Math.atan2(Math.cos(eps) * Math.sin(lam), Math.cos(lam));
  const gmst = mod(18.697374558 + 24.06570982441908 * n, 24);
  const ha = (mod(gmst * 15 + LON - ra / rad + 540, 360) - 180) * rad;
  const lat = LAT * rad;
  return Math.asin(Math.sin(lat) * Math.sin(dec) + Math.cos(lat) * Math.cos(dec) * Math.cos(ha)) / rad;
}

export function clearSkyGhi(elevation: number) {
  const s = Math.sin((Math.min(Math.max(elevation, 0), 90) * Math.PI) / 180);
  return s > 0 ? 1098 * s * Math.exp(-0.057 / s) : 0;
}

export function timeFeatures(time: number) {
  const local = localParts(time);
  const hour = local.hour + local.minute / 60;
  const doy = Math.round((Date.UTC(local.year, local.month - 1, local.day) - Date.UTC(local.year, 0, 1)) / 86_400_000) + 1;
  const sunElev = solarElevation(time);
  return {
    hour_local: hour,
    hour_sin: Math.sin((2 * Math.PI * hour) / 24),
    hour_cos: Math.cos((2 * Math.PI * hour) / 24),
    doy_sin: Math.sin((2 * Math.PI * doy) / 365.25),
    doy_cos: Math.cos((2 * Math.PI * doy) / 365.25),
    sun_elev: sunElev,
    clear_sky: clearSkyGhi(sunElev),
  };
}

// ------------------------------------------------------------------------------------------
// Climate day helpers (NWS climate day = midnight-to-midnight local STANDARD time = UTC-5)
// ------------------------------------------------------------------------------------------

export function climateDate(time: number) {
  return new Date(time - 5 * HOUR).toISOString().slice(0, 10);
}

/** Decision times are (dayStart, dayStart + 24h]; returns the day that owns `tau`. */
export function climateDayStart(tau: number) {
  const date = climateDate(tau - MINUTE);
  return Date.parse(`${date}T05:00:00Z`);
}

// ------------------------------------------------------------------------------------------
// Airports west/south of the park (EWR, CDW, JRB) - mirrors research/manh_knyc/airports.py
// ------------------------------------------------------------------------------------------

type AirportSeries = {
  temp: Float64Array; // forward-filled up to 3 stamps (pandas ffill(limit=3))
  rawTemp: Float64Array;
  u: Float64Array; // rolling(3, min_periods=1) mean of wind components (knots)
  v: Float64Array;
  metars: AirportMetar[]; // sorted by valid
};

const AIRPORT_TEMP_RANGE_F = [-40, 120] as const; // drops the M61/M61 (-61 C) missing-data sentinel
const AIRPORT_METAR_MAX_AGE = [90 * MINUTE, 180 * MINUTE] as const; // last / previous METAR freshness

export type AirportData = { start: number; length: number; latencyMs: number; metarLagMs: number; stations: Record<string, AirportSeries> };

export function buildAirportData(
  model: NowcastModel,
  hf: AirportHfObservation[],
  metars: AirportMetar[],
  start: number,
  end: number,
): AirportData | null {
  if (!model.airports) return null;
  const length = Math.floor((end - start) / STEP_MS) + 1;
  const stations: Record<string, AirportSeries> = {};
  for (const code of model.airports.stations) {
    const raw = new Float64Array(length).fill(nan);
    const uRaw = new Float64Array(length).fill(nan);
    const vRaw = new Float64Array(length).fill(nan);
    for (const obs of hf) {
      if (obs.station !== code) continue;
      const slot = (obs.time - start) / STEP_MS;
      if (!Number.isInteger(slot) || slot < 0 || slot >= length) continue;
      if (typeof obs.tempF === "number" && obs.tempF >= AIRPORT_TEMP_RANGE_F[0] && obs.tempF <= AIRPORT_TEMP_RANGE_F[1]) {
        raw[slot] = obs.tempF;
      }
      if (typeof obs.dirDeg === "number" && typeof obs.speedKt === "number") {
        const rad = (obs.dirDeg * Math.PI) / 180;
        uRaw[slot] = -obs.speedKt * Math.sin(rad);
        vRaw[slot] = -obs.speedKt * Math.cos(rad);
      }
    }
    const temp = Float64Array.from(raw);
    let lastValue = nan;
    let gap = 0;
    for (let i = 0; i < length; i += 1) {
      if (isNum(raw[i])) {
        lastValue = raw[i];
        gap = 0;
      } else {
        gap += 1;
        if (isNum(lastValue) && gap <= 3) temp[i] = lastValue;
      }
    }
    const roll = (series: Float64Array) =>
      series.map((_, i) => {
        let sum = 0;
        let count = 0;
        for (let k = Math.max(0, i - 2); k <= i; k += 1) {
          if (isNum(series[k])) {
            sum += series[k];
            count += 1;
          }
        }
        return count ? sum / count : nan;
      });
    stations[code] = {
      temp,
      rawTemp: raw,
      u: roll(uRaw),
      v: roll(vRaw),
      metars: metars
        .filter((m) => m.station === code && m.temp >= AIRPORT_TEMP_RANGE_F[0] && m.temp <= AIRPORT_TEMP_RANGE_F[1])
        .sort((a, b) => a.valid - b.valid),
    };
  }
  return {
    start,
    length,
    latencyMs: model.airports.hf_latency_min * MINUTE,
    metarLagMs: model.airports.metar_lag_min * MINUTE,
    stations,
  };
}

function airportAt(air: AirportData, series: Float64Array, time: number) {
  const slot = (time - air.start) / STEP_MS;
  return Number.isInteger(slot) && slot >= 0 && slot < air.length ? series[slot] : nan;
}

/** ref = last KNYC METAR (next-METAR model) or the published max (daily models). */
export function airportFeatures(air: AirportData | null, codes: string[], tau: number, ref: number, cdate?: string) {
  const out: Record<string, number> = {};
  const lvls: number[] = [];
  const d30s: number[] = [];
  for (const code of codes) {
    const st = air?.stations[code];
    const avail = Math.floor((tau - (air?.latencyMs ?? 0)) / STEP_MS) * STEP_MS;
    const T = (time: number) => (st && air ? airportAt(air, st.temp, time) : nan);
    const cur = T(avail);
    out[`${code}_hf_lvl`] = cur - ref;
    out[`${code}_hf_d30`] = cur - T(avail - 30 * MINUTE);
    out[`${code}_hf_d60`] = cur - T(avail - HOUR);
    out[`${code}_u`] = st && air ? airportAt(air, st.u, avail) : nan;
    out[`${code}_v`] = st && air ? airportAt(air, st.v, avail) : nan;
    const visible = st && air ? st.metars.filter((m) => m.valid + air.metarLagMs <= tau) : [];
    const fresh = (m: AirportMetar | undefined, maxAge: number) => (m && tau - m.valid <= maxAge ? m.temp : nan);
    const last = fresh(visible.at(-1), AIRPORT_METAR_MAX_AGE[0]);
    const prev = fresh(visible.at(-2), AIRPORT_METAR_MAX_AGE[1]);
    out[`${code}_met_lvl`] = last - ref;
    out[`${code}_met_d60`] = last - prev;
    if (cdate !== undefined) {
      let dayMax = -Infinity;
      if (st && air) {
        for (let slot = 0; slot < air.length; slot += 1) {
          const stamp = air.start + slot * STEP_MS;
          const value = st.rawTemp[slot];
          if (isNum(value) && stamp + air.latencyMs <= tau && climateDate(stamp) === cdate) dayMax = Math.max(dayMax, value);
        }
        for (const m of visible) if (climateDate(m.valid) === cdate) dayMax = Math.max(dayMax, m.temp);
      }
      out[`${code}_daymax_lvl`] = isNum(dayMax) ? dayMax - ref : nan;
    }
    lvls.push(out[`${code}_hf_lvl`]);
    d30s.push(out[`${code}_hf_d30`]);
  }
  const meanFinite = (values: number[]) => {
    const finite = values.filter(isNum);
    return finite.length ? finite.reduce((a, b) => a + b, 0) / finite.length : nan;
  };
  out.air_hf_lvl = meanFinite(lvls);
  out.air_hf_d30 = meanFinite(d30s);
  return out;
}

// ------------------------------------------------------------------------------------------
// Kalshi NYC weather index - mirrors research/manh_knyc/kalshi_index.py
// ------------------------------------------------------------------------------------------

export type KalshiIndexSeries = { start: number; length: number; latencyMs: number; raw: Float64Array };

/**
 * 5-minute index series. Only stamps with all eight contributors under the expected formula
 * version are kept; anything else (degraded minutes, a changed formula) becomes missing.
 */
export function buildIndexSeries(model: NowcastModel, feed: KalshiIndexFeed | null, start: number, end: number) {
  const cfg = model.kalshi_index;
  if (!cfg) return null;
  const length = Math.floor((end - start) / STEP_MS) + 1;
  const raw = new Float64Array(length).fill(nan);
  if (feed && feed.configVersion.startsWith(cfg.config_prefix)) {
    for (const point of feed.points) {
      if (point.contributors !== cfg.stations.length || !isNum(point.v)) continue;
      const slot = (point.t - start) / STEP_MS;
      if (Number.isInteger(slot) && slot >= 0 && slot < length) raw[slot] = point.v;
    }
  }
  return { start, length, latencyMs: cfg.latency_min * MINUTE, raw };
}

export function indexFeatures(series: KalshiIndexSeries | null, tau: number, ref: number, cdate?: string) {
  const rawAt = (time: number) => {
    if (!series) return nan;
    const slot = (time - series.start) / STEP_MS;
    return Number.isInteger(slot) && slot >= 0 && slot < series.length ? series.raw[slot] : nan;
  };
  const filled = (time: number) => {
    const value = rawAt(time);
    return isNum(value) ? value : rawAt(time - STEP_MS); // pandas ffill(limit=1)
  };
  const avail = Math.floor((tau - (series?.latencyMs ?? 0)) / STEP_MS) * STEP_MS;
  const cur = filled(avail);
  const out: Record<string, number> = {
    kidx_lvl: cur - ref,
    kidx_d15: cur - filled(avail - 15 * MINUTE),
    kidx_d30: cur - filled(avail - 30 * MINUTE),
    kidx_d60: cur - filled(avail - HOUR),
  };
  if (cdate !== undefined) {
    let dayMax = -Infinity;
    if (series) {
      for (let slot = 0; slot < series.length; slot += 1) {
        const stamp = series.start + slot * STEP_MS;
        const value = series.raw[slot];
        if (isNum(value) && stamp + series.latencyMs <= tau && climateDate(stamp) === cdate) dayMax = Math.max(dayMax, value);
      }
    }
    out.kidx_daymax_lvl = isNum(dayMax) ? dayMax - ref : nan;
  }
  return out;
}

// ------------------------------------------------------------------------------------------
// Model A: next routine METAR
// ------------------------------------------------------------------------------------------

export type NextMetarFeatures = Record<string, number>;

export function nextMetarFeatures(
  model: NowcastModel,
  grid: Grid,
  reports: KnycReport[],
  t0: number,
  tau: number,
  air: AirportData | null = null,
  kidx: KalshiIndexSeries | null = null,
) {
  const routine = new Map(reports.filter((report) => report.routine).map((report) => [report.valid, report.temp]));
  const y0 = routine.get(t0);
  if (y0 === undefined) return null;
  const step = (tau - t0 - 4 * MINUTE) / STEP_MS;
  if (!Number.isInteger(step) || step < 0 || step > 11) return null;
  const yPrev = routine.get(t0 - HOUR) ?? nan;
  const yPrev2 = routine.get(t0 - 2 * HOUR) ?? nan;
  const manh = grid.temps.MANH;
  const M = (time: number) => at(manh, grid, time);
  const m0 = M(t0 - MINUTE);
  const steps = Array.from({ length: step + 1 }, (_, k) => t0 + 4 * MINUTE + k * STEP_MS);
  const finiteMax = (values: number[]) => {
    const finite = values.filter(isNum);
    return finite.length ? Math.max(...finite) : nan;
  };
  const finiteMin = (values: number[]) => {
    const finite = values.filter(isNum);
    return finite.length ? Math.min(...finite) : nan;
  };
  const anchor = stationVector(model, grid, t0 - MINUTE);
  const dzPath = steps.map((time) => blendChange(model, anchor, stationVector(model, grid, time)));
  const srad30 = (time: number) => rollingMean(grid.srad, grid, time, 6, 3);
  const tf = timeFeatures(tau);
  const wdir = at(grid.wdir, grid, tau);
  const features: NextMetarFeatures = {
    y0,
    d_prev: y0 - yPrev,
    d_prev2: yPrev - yPrev2,
    ...tf,
    lead_min: 56 - 5 * step,
    dm: M(tau) - m0,
    dm5: M(tau) - M(tau - 5 * MINUTE),
    dm15: M(tau) - M(tau - 15 * MINUTE),
    dmax: finiteMax(steps.map(M)) - m0,
    dmin: finiteMin(steps.map(M)) - m0,
    dm_prevhr: m0 - M(t0 - 61 * MINUTE),
    dm_pre30: m0 - M(t0 - 31 * MINUTE),
    offset: m0 - y0,
    m0,
    dz: dzPath[step],
    dz15: blendChange(model, stationVector(model, grid, tau - 15 * MINUTE), stationVector(model, grid, tau)),
    dzmax: finiteMax(dzPath),
    dzmin: finiteMin(dzPath),
    dz_prevhr: blendChange(model, stationVector(model, grid, t0 - 61 * MINUTE), anchor),
    srad: at(grid.srad, grid, tau),
    srad30: srad30(tau),
    srad_d30: srad30(tau) - srad30(tau - 30 * MINUTE),
    kt: srad30(tau) / Math.max(tf.clear_sky, 50),
    wspd: rollingMean(grid.wspd, grid, tau, 3, 1),
    wdir_sin: Math.sin((wdir * Math.PI) / 180),
    wdir_cos: Math.cos((wdir * Math.PI) / 180),
    relh: at(grid.relh, grid, tau),
    pres_d60: at(grid.pres, grid, tau) - at(grid.pres, grid, tau - HOUR),
    precip30: rollingSumFilled(grid.precipInc, grid, tau, 6),
  };
  for (const station of model.stations) {
    if (station === "MANH") continue;
    const series = grid.temps[station];
    features[`${station}_dm`] = at(series, grid, tau) - at(series, grid, t0 - MINUTE);
    features[`${station}_off`] = at(series, grid, t0 - MINUTE) - y0;
  }
  if (model.airports) Object.assign(features, airportFeatures(air, model.airports.stations, tau, y0));
  if (model.kalshi_index) Object.assign(features, indexFeatures(kidx, tau, y0));
  return features;
}

// ------------------------------------------------------------------------------------------
// Model B: hidden running max / final CLI high
// ------------------------------------------------------------------------------------------

const BETAS = [0.5, 1.0] as const;

function pathFeatures(
  model: NowcastModel,
  grid: Grid,
  weights: Record<string, number>,
  routine: KnycReport[],
  tau: number,
  publishLag: number,
  prefix: string,
) {
  const out: Record<string, number> = {};
  let exc = -Infinity;
  const imp: Record<number, number> = { 0.5: -Infinity, 1: -Infinity };
  const vec = (time: number) => stationVector(model, grid, time);
  for (let i = 0; i < routine.length - 1; i += 1) {
    const ta = routine[i].valid;
    const tb = routine[i + 1].valid;
    if (tb - ta !== HOUR || tau < tb + publishLag) continue;
    const a = vec(ta - MINUTE);
    const b = vec(tb - MINUTE);
    let localExc = -Infinity;
    const localImp: Record<number, number> = { 0.5: -Infinity, 1: -Infinity };
    for (let k = 1; k <= 11; k += 1) {
      const frac = k / 12;
      const line = a.map((value, index) => value + (b[index] - value) * frac);
      const dev = blendChange(model, line, vec(ta - MINUTE + k * STEP_MS), weights);
      if (!isNum(dev)) continue;
      localExc = Math.max(localExc, dev);
      const yline = routine[i].temp + (routine[i + 1].temp - routine[i].temp) * frac;
      for (const beta of BETAS) localImp[beta] = Math.max(localImp[beta], yline + beta * dev);
    }
    exc = Math.max(exc, localExc);
    for (const beta of BETAS) imp[beta] = Math.max(imp[beta], localImp[beta]);
  }
  const published = routine.filter((report) => report.valid + publishLag <= tau);
  const last = published.at(-1);
  let dzOpen = nan;
  let dzMaxOpen = nan;
  if (last) {
    const anchor = vec(last.valid - MINUTE);
    if (anchor.some(isNum)) {
      let runMax = -Infinity;
      for (let time = last.valid - MINUTE + STEP_MS; time <= tau; time += STEP_MS) {
        const value = blendChange(model, anchor, vec(time), weights);
        if (isNum(value)) runMax = Math.max(runMax, value);
      }
      dzOpen = blendChange(model, anchor, vec(tau), weights);
      dzMaxOpen = isNum(runMax) ? runMax : nan;
    }
  }
  const yLast = last ? last.temp : nan;
  const fin = (value: number) => (isNum(value) ? value : nan);
  out[`${prefix}exc_closed`] = fin(exc);
  out[`${prefix}dz_open`] = dzOpen;
  out[`${prefix}dzmax_open`] = dzMaxOpen;
  for (const beta of BETAS) {
    out[`${prefix}imp_closed_b${beta.toFixed(1)}`] = fin(imp[beta]);
    out[`${prefix}imp_open_b${beta.toFixed(1)}`] = yLast + beta * dzMaxOpen;
    out[`${prefix}imp_now_b${beta.toFixed(1)}`] = yLast + beta * dzOpen;
  }
  return { out, yLast };
}

export type DayFeatures = Record<string, number>;

export function dayFeatures(
  model: NowcastModel,
  grid: Grid,
  reports: KnycReport[],
  nbm: NbmRun[],
  tau: number,
  air: AirportData | null = null,
  kidx: KalshiIndexSeries | null = null,
) {
  const dayStart = climateDayStart(tau);
  const cdate = climateDate(dayStart);
  const lag = model.publish_lag_min * MINUTE;
  const today = reports.filter((report) => climateDate(report.valid) === cdate).sort((a, b) => a.valid - b.valid);
  const visible = today.filter((report) => report.valid + lag <= tau);
  if (!visible.length) return null;
  const knownMetar = Math.max(...visible.map((report) => report.temp));
  const sixValues = visible
    .filter((report) => report.max6 !== null && [11, 17, 23].includes(new Date(report.valid).getUTCHours()))
    .map((report) => report.max6 as number);
  const six = sixValues.length ? Math.max(...sixValues) : -Infinity;
  const knownMax = Math.max(knownMetar, six);
  const routine = today.filter((report) => report.routine);
  const manhOnly = Object.fromEntries(model.stations.map((station) => [station, station === "MANH" ? 1 : 0]));
  const m = pathFeatures(model, grid, manhOnly, routine, tau, lag, "m_");
  const z = pathFeatures(model, grid, model.blend_weights, routine, tau, lag, "z_");
  const manh = grid.temps.MANH;
  let manhDayMax = -Infinity;
  for (let time = dayStart + STEP_MS; time <= tau; time += STEP_MS) {
    const value = at(manh, grid, time);
    if (isNum(value)) manhDayMax = Math.max(manhDayMax, value);
  }
  const mNow = at(manh, grid, tau);
  const runs = nbm
    .filter((run) => run.cdate === cdate && run.runtime + model.nbm_lag_min * MINUTE <= tau)
    .sort((a, b) => a.runtime - b.runtime);
  const nbmRun = runs.at(-1);
  const txn = nbmRun ? nbmRun.txn : nan;
  const tf = timeFeatures(tau);
  const srad30 = rollingMean(grid.srad, grid, tau, 6, 3);
  const features: DayFeatures = {
    known_max: knownMax,
    y_below: m.yLast - knownMax,
    six_known: isNum(six) ? 1 : 0,
    mins_into_day: (tau - dayStart) / MINUTE,
    txn_gap: txn - knownMax,
    xnd: nbmRun?.xnd ?? nan,
    ...tf,
    ...m.out,
    ...z.out,
    m_below_daymax: mNow - (isNum(manhDayMax) ? manhDayMax : nan),
    m_slope30: mNow - at(manh, grid, tau - 30 * MINUTE),
    srad: at(grid.srad, grid, tau),
    srad30,
    kt: srad30 / Math.max(tf.clear_sky, 50),
  };
  for (const prefix of ["m_", "z_"]) {
    for (const beta of BETAS) {
      const b = beta.toFixed(1);
      features[`${prefix}gap_closed_b${b}`] = features[`${prefix}imp_closed_b${b}`] - knownMax;
      features[`${prefix}gap_open_b${b}`] = features[`${prefix}imp_open_b${b}`] - knownMax;
      features[`${prefix}gap_now_b${b}`] = features[`${prefix}imp_now_b${b}`] - knownMax;
    }
  }
  if (model.airports) Object.assign(features, airportFeatures(air, model.airports.stations, tau, knownMax, cdate));
  if (model.kalshi_index) Object.assign(features, indexFeatures(kidx, tau, knownMax, cdate));
  return { features, knownMax, knownMetar, txn, nbmRun: nbmRun ?? null, cdate, dayStart };
}

// ------------------------------------------------------------------------------------------
// Predictions
// ------------------------------------------------------------------------------------------

export type Distribution = Array<{ value: number; p: number }>;

function toDistribution(probs: number[], offset: number, anchor: number): Distribution {
  return probs.map((p, index) => ({ value: anchor + offset + index, p }));
}

export function predictNextMetar(model: NowcastModel, features: NextMetarFeatures): Distribution {
  const booster = model.next_metar;
  const probs = predictProba(booster, featureVector(booster, features));
  return toDistribution(probs, booster.class_offset, features.y0);
}

export function predictHiddenMax(model: NowcastModel, features: DayFeatures) {
  const booster = model.hidden_max;
  return applyPlatt(predictProba(booster, featureVector(booster, features)), booster.platt_ge1 ?? null);
}

/** Rescale [P(+0), P(+1), P(+2), P(+3+)] so P(>= +1) follows the exported Platt correction. */
export function applyPlatt(probs: number[], platt: [number, number] | null): number[] {
  if (!platt) return probs;
  const above = Math.min(Math.max(1 - probs[0], 1e-6), 1 - 1e-6);
  const corrected = 1 / (1 + Math.exp(-(platt[0] * Math.log(above / (1 - above)) + platt[1])));
  return [1 - corrected, ...probs.slice(1).map((p) => (p * corrected) / above)];
}

/** Final CLI high: the model predicts how many degrees the CLI high ends above the published max. */
export function predictFinalHigh(model: NowcastModel, features: DayFeatures, knownMax: number): Distribution {
  const booster = model.final_high;
  // Refuse a model exported with a different target definition rather than mislabel its classes.
  if (booster.anchor !== "known_max") throw new Error(`Unsupported final_high anchor: ${booster.anchor}`);
  return toDistribution(predictProba(booster, featureVector(booster, features)), booster.class_offset, knownMax);
}

/** Most recent grid time whose observation has had time to arrive. */
export function latestGridTime(grid: Grid, station = "MANH") {
  const series = grid.temps[station];
  for (let slot = grid.length - 1; slot >= 0; slot -= 1) {
    if (isNum(series[slot])) return grid.start + slot * STEP_MS;
  }
  return null;
}

export function parseKnycMetar(raw: string, valid: number, fallbackTempC: number | null): KnycReport | null {
  const remarks = raw.includes(" RMK ") ? raw.slice(raw.indexOf(" RMK ")) : "";
  const tGroup = / T([01])(\d{3})([01])(\d{3})/.exec(raw);
  const toWholeF = (sign: string, digits: string) => {
    const celsius = (Number(digits) / 10) * (sign === "1" ? -1 : 1);
    return Math.round((celsius * 9) / 5 + 32);
  };
  let temp: number | null = tGroup ? toWholeF(tGroup[1], tGroup[2]) : null;
  if (temp === null && fallbackTempC !== null) temp = Math.round((fallbackTempC * 9) / 5 + 32);
  if (temp === null) return null;
  const six = / 1([01])(\d{3})(?=\s|$)/.exec(remarks);
  const minute = new Date(valid).getUTCMinutes();
  return {
    valid,
    temp,
    max6: six ? toWholeF(six[1], six[2]) : null,
    routine: minute === 51 && tGroup !== null && !raw.startsWith("SPECI"),
    raw,
  };
}
