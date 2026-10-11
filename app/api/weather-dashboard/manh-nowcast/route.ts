import { readFile } from "node:fs/promises";
import path from "node:path";
import { NextResponse } from "next/server";
import { AWC_BASE_URL, IEM_BASE_URL, KALSHI_BASE_URL } from "@/lib/config";
import { fetchJson, fetchText } from "@/lib/http";
import { marketToBand, type KalshiEventDetail } from "@/lib/sources/kalshi";
import { getAirportHf, getAirportMetars } from "@/lib/sources/airport-feeds";
import { getNysmTimeseries } from "@/lib/sources/nysm";
import {
  MINUTE,
  STEP_MS,
  blendChange,
  buildAirportData,
  buildGrid,
  climateDate,
  dayFeatures,
  latestGridTime,
  nextMetarFeatures,
  parseKnycMetar,
  predictFinalHigh,
  predictHiddenMax,
  predictNextMetar,
  type AirportHfObservation,
  type AirportMetar,
  type Distribution,
  type Grid,
  type KnycReport,
  type MesonetObservation,
  type NbmRun,
  type NowcastModel,
} from "@/lib/weather/manh-nowcast";

export const dynamic = "force-dynamic";

const HOUR = 60 * MINUTE;
const LOOKBACK = 30 * HOUR;
const STALE_MS = 20 * MINUTE;
const MANH_VARS = ["tair", "srad", "relh", "pres", "wspd_sonic", "wdir_sonic", "precip"];

declare global {
  var manhNowcastModelV1: Promise<NowcastModel> | undefined;
  var manhNowcastCacheV1: { at: number; body: unknown } | undefined;
}

function loadModel() {
  globalThis.manhNowcastModelV1 ??= readFile(path.join(process.cwd(), "lib", "weather", "manh-nowcast-model.json"), "utf8")
    .then((text) => JSON.parse(text) as NowcastModel);
  return globalThis.manhNowcastModelV1;
}

type AwcReport = { obsTime: number; rawOb: string; temp?: number; receiptTime?: string };

async function getKnycReports(): Promise<Array<KnycReport & { receivedAt: string | null }>> {
  const params = new URLSearchParams({ ids: "KNYC", format: "json", hours: "32" });
  const rows = await fetchJson<AwcReport[]>(`${AWC_BASE_URL}/metar?${params}`, { retries: 2 });
  const out: Array<KnycReport & { receivedAt: string | null }> = [];
  for (const row of rows) {
    const parsed = parseKnycMetar(row.rawOb, row.obsTime * 1000, row.temp ?? null);
    if (parsed) out.push({ ...parsed, receivedAt: row.receiptTime ?? null });
  }
  const seen = new Set<number>();
  return out
    .sort((a, b) => a.valid - b.valid)
    .filter((row) => (seen.has(row.valid) ? false : (seen.add(row.valid), true)));
}

async function getNbmRuns(now: number): Promise<NbmRun[]> {
  const sts = new Date(now - 36 * HOUR).toISOString().slice(0, 16) + "Z";
  const ets = new Date(now + HOUR).toISOString().slice(0, 16) + "Z";
  const csv = await fetchText(`${IEM_BASE_URL}/cgi-bin/request/mos.py?station=KNYC&model=NBS&sts=${sts}&ets=${ets}&format=csv`, {
    retries: 1,
    timeoutMs: 15_000,
  });
  const [header, ...lines] = csv.trim().split(/\r?\n/);
  const cols = header.split(",");
  const ix = (name: string) => cols.indexOf(name);
  const runs: NbmRun[] = [];
  for (const line of lines) {
    const cells = line.split(",");
    const txn = Number(cells[ix("txn")]);
    const ftime = Date.parse(cells[ix("ftime")].replace(" ", "T") + "Z");
    if (!cells[ix("txn")] || !Number.isFinite(txn) || new Date(ftime).getUTCHours() !== 0) continue;
    const xnd = Number(cells[ix("xnd")]);
    runs.push({
      runtime: Date.parse(cells[ix("runtime")].replace(" ", "T") + "Z"),
      cdate: new Date(ftime - 12 * HOUR).toISOString().slice(0, 10),
      txn,
      xnd: cells[ix("xnd")] && Number.isFinite(xnd) ? xnd : null,
    });
  }
  return runs;
}

const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

async function getKalshiBands(cdate: string) {
  const [year, month, day] = cdate.split("-");
  const ticker = `KXHIGHNY-${year.slice(2)}${MONTHS[Number(month) - 1]}${day}`;
  const payload = await fetchJson<KalshiEventDetail>(`${KALSHI_BASE_URL}/events/${ticker}?with_nested_markets=true`, { retries: 1 });
  const num = (value: unknown) => (value === undefined || value === null || value === "" ? null : Number(value));
  return {
    ticker,
    bands: (payload.event.markets ?? []).map((market) => {
      const band = marketToBand(market);
      const record = market as Record<string, unknown>;
      return { ...band, yesBid: num(record.yes_bid_dollars), yesAsk: num(record.yes_ask_dollars) };
    }),
  };
}

function summarize(dist: Distribution) {
  const mode = dist.reduce((best, item) => (item.p > best.p ? item : best), dist[0]);
  const mean = dist.reduce((sum, item) => sum + item.value * item.p, 0);
  return { mode: mode.value, pMode: mode.p, mean };
}

function impliedPath(model: NowcastModel, grid: Grid, routine: KnycReport[], dayStart: number, tau: number) {
  const vec = (time: number) => model.stations.map((station) => {
    const slot = (time - grid.start) / STEP_MS;
    return slot >= 0 && slot < grid.length ? grid.temps[station][slot] : Number.NaN;
  });
  const points: Array<{ time: number; implied: number | null; manh: number | null }> = [];
  for (let time = dayStart + STEP_MS; time <= tau; time += STEP_MS) {
    const done = routine.filter((report) => report.valid - MINUTE <= time);
    const left = done.at(-1);
    const right = routine.find((report) => report.valid - MINUTE > time);
    let implied: number | null = null;
    if (left) {
      const a = vec(left.valid - MINUTE);
      if (right && right.valid - left.valid === HOUR) {
        const b = vec(right.valid - MINUTE);
        const frac = (time - (left.valid - MINUTE)) / HOUR;
        const line = a.map((value, index) => value + (b[index] - value) * frac);
        const dev = blendChange(model, line, vec(time));
        implied = Number.isFinite(dev) ? left.temp + (right.temp - left.temp) * frac + dev : null;
      } else {
        const dz = blendChange(model, a, vec(time));
        implied = Number.isFinite(dz) ? left.temp + dz : null;
      }
    }
    const slot = (time - grid.start) / STEP_MS;
    const manh = grid.temps.MANH[slot];
    points.push({ time, implied, manh: Number.isFinite(manh) ? manh : null });
  }
  return points;
}

export async function GET() {
  const now = Date.now();
  const cached = globalThis.manhNowcastCacheV1;
  if (cached && now - cached.at < 30_000) return NextResponse.json(cached.body, { headers: { "Cache-Control": "no-store" } });

  try {
    const model = await loadModel();
    const end = Math.floor(now / STEP_MS) * STEP_MS;
    const start = end - LOOKBACK;
    const nysmStations = model.stations.filter((station) => model.station_datasets[station] === "nysm");
    const nycStations = model.stations.filter((station) => model.station_datasets[station] === "nyc");
    const airportCodes = model.airports?.stations ?? [];
    const [nysm, nyc, reports, nbm, airportHf, airportMetars] = await Promise.all([
      getNysmTimeseries("nysm", nysmStations, MANH_VARS, start, end),
      getNysmTimeseries("nyc", nycStations, ["tair"], start, end).catch(() => [] as MesonetObservation[]),
      getKnycReports(),
      getNbmRuns(now).catch(() => [] as NbmRun[]),
      airportCodes.length ? getAirportHf(airportCodes, start) : Promise.resolve([] as AirportHfObservation[]),
      airportCodes.length
        ? getAirportMetars(airportCodes, 32).catch(() => [] as AirportMetar[])
        : Promise.resolve([] as AirportMetar[]),
    ]);
    const air = buildAirportData(model, airportHf, airportMetars, start, end);
    const observations = [...nysm, ...nyc].map((obs) => (obs.station === "MANH" ? obs : { ...obs, srad: null }));
    const grid = buildGrid(observations, model.stations, start, end);
    const tau = latestGridTime(grid);
    if (tau === null) throw new Error("No recent MANH observations");
    // Never predict from an old MANH reading: the features assume tau is "now".
    const stale = now - tau > STALE_MS;
    const staleReason = `MANH has not reported since ${new Date(tau).toISOString().slice(11, 16)}Z`;

    const stationsNow = model.stations.map((station) => {
      const series = grid.temps[station];
      let latest: { time: number; temp: number } | null = null;
      for (let slot = grid.length - 1; slot >= 0 && !latest; slot -= 1) {
        if (Number.isFinite(series[slot])) latest = { time: grid.start + slot * STEP_MS, temp: series[slot] };
      }
      return { station, dataset: model.station_datasets[station], weight: model.blend_weights[station], latest };
    });
    const airportsNow = airportCodes.map((code) => {
      const hf = airportHf.filter((obs) => obs.station === code && obs.tempF !== null).sort((a, b) => a.time - b.time).at(-1);
      const metar = airportMetars.filter((obs) => obs.station === code).sort((a, b) => a.valid - b.valid).at(-1);
      return {
        station: code,
        hf: hf ? { time: hf.time, temp: hf.tempF } : null,
        metar: metar ? { time: metar.valid, temp: metar.temp } : null,
      };
    });

    // ---- next routine METAR ----
    const routine = reports.filter((report) => report.routine);
    const lastRoutine = routine.filter((report) => report.valid + model.publish_lag_min * MINUTE <= now).at(-1) ?? null;
    let nextMetar: Record<string, unknown> = {
      status: "unavailable",
      reason: stale ? staleReason : "No recent routine KNYC METAR",
    };
    if (lastRoutine && !stale) {
      const t0 = lastRoutine.valid;
      const dueAt = t0 + HOUR;
      if (tau < t0 + 4 * MINUTE) {
        nextMetar = { status: "waiting", reason: "Waiting for the first Mesonet reading after the last METAR", dueAt };
      } else if (tau > t0 + 59 * MINUTE) {
        nextMetar = { status: "waiting", reason: "Next METAR is due; waiting for it to publish", dueAt };
      } else {
        const features = nextMetarFeatures(model, grid, reports, t0, tau, air);
        if (!features) {
          nextMetar = { status: "unavailable", reason: "Feature window incomplete", dueAt };
        } else {
          const distribution = predictNextMetar(model, features).filter((item) => item.p >= 0.0005);
          const same = distribution.find((item) => item.value === lastRoutine.temp)?.p ?? 0;
          nextMetar = {
            status: "ok",
            t0,
            tau,
            dueAt,
            lastTemp: lastRoutine.temp,
            distribution,
            pSame: same,
            pUp: distribution.filter((item) => item.value > lastRoutine.temp).reduce((sum, item) => sum + item.p, 0),
            pDown: distribution.filter((item) => item.value < lastRoutine.temp).reduce((sum, item) => sum + item.p, 0),
            ...summarize(distribution),
            drivers: {
              manhChange: features.dm,
              blendChange: features.dz,
              impliedNow: lastRoutine.temp + features.dz,
              leadMinutes: features.lead_min,
            },
          };
        }
      }
    }

    // ---- hidden running max and final CLI high ----
    let day: Record<string, unknown> = {
      status: "unavailable",
      reason: stale ? staleReason : "No KNYC report yet in this climate day",
    };
    const dayResult = stale ? null : dayFeatures(model, grid, reports, nbm, tau, air);
    if (dayResult) {
      const hidden = predictHiddenMax(model, dayResult.features);
      const finalDist = predictFinalHigh(model, dayResult.features, dayResult.knownMax).filter((item) => item.p >= 0.0005);
      const dayRoutine = routine.filter((report) => climateDate(report.valid) === dayResult.cdate);
      const today = reports.filter((report) => climateDate(report.valid) === dayResult.cdate);
      let kalshi: Awaited<ReturnType<typeof getKalshiBands>> | null = null;
      try {
        kalshi = await getKalshiBands(dayResult.cdate);
      } catch {
        kalshi = null;
      }
      const bands = kalshi?.bands.map((band) => {
        const p = finalDist
          .filter((item) => (band.lower === null || item.value >= band.lower) && (band.upper === null || item.value <= band.upper))
          .reduce((sum, item) => sum + item.p, 0);
        const mid = band.yesBid !== null && band.yesAsk !== null ? (band.yesBid + band.yesAsk) / 2 : null;
        return { ...band, model: p, marketMid: mid };
      });
      day = {
        status: "ok",
        cdate: dayResult.cdate,
        dayStart: dayResult.dayStart,
        knownMax: dayResult.knownMax,
        knownMetar: dayResult.knownMetar,
        nbmTxn: Number.isFinite(dayResult.txn) ? dayResult.txn : null,
        nbmSpread: dayResult.nbmRun?.xnd ?? null,
        nbmRuntime: dayResult.nbmRun?.runtime ?? null,
        hidden: { p0: hidden[0], p1: hidden[1], p2: hidden[2], p3plus: hidden[3], pAbove: 1 - hidden[0] },
        finalDistribution: finalDist,
        final: summarize(finalDist),
        kalshi: kalshi ? { ticker: kalshi.ticker, bands } : null,
        reports: today.map((report) => ({ valid: report.valid, temp: report.temp, routine: report.routine, max6: report.max6, raw: report.raw })),
        path: impliedPath(model, grid, dayRoutine, dayResult.dayStart, tau),
      };
    }

    const body = {
      generatedAt: now,
      tau,
      model: { version: model.version, trainedThrough: model.trained_through, stations: model.stations },
      stations: stationsNow,
      airports: airportsNow,
      lastMetar: lastRoutine ? { valid: lastRoutine.valid, temp: lastRoutine.temp, raw: lastRoutine.raw, receivedAt: lastRoutine.receivedAt } : null,
      nextMetar,
      day,
    };
    globalThis.manhNowcastCacheV1 = { at: now, body };
    return NextResponse.json(body, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    console.error("MANH nowcast failed", error);
    return NextResponse.json({ error: "MANH nowcast is temporarily unavailable." }, { status: 503 });
  }
}
