import { fetchJson } from "../http";
import type { MesonetObservation } from "../weather/manh-nowcast";

// NYS Mesonet public timeseries API (the same endpoint nysmesonet.org's meteograms use).
// Requests are limited to 60 days; values arrive ~2 minutes after each 5-minute stamp.
const NYSM_TIMESERIES_URL = "https://api.nysmesonet.org/data/dynserv/timeseries2";

type DataVar = { attrs?: { scale_factor?: number }; data: Array<Array<number | null>>; dims: string[] };
type TimeseriesResponse = {
  success: boolean;
  response: {
    coords: { station: { data: string[] }; time: { data: string[] } };
    data_vars: Record<string, DataVar>;
  };
};

const VARIABLE_MAP: Record<string, keyof MesonetObservation> = {
  tair: "tair",
  srad: "srad",
  relh: "relh",
  pres: "pres",
  wspd_sonic: "wspd",
  wdir_sonic: "wdir",
  precip: "precip",
};

function isoMinute(ms: number) {
  return new Date(ms).toISOString().slice(0, 19) + ".000";
}

function parseStamp(stamp: string) {
  // 20261010T1520 -> epoch ms (UTC)
  return Date.UTC(
    Number(stamp.slice(0, 4)),
    Number(stamp.slice(4, 6)) - 1,
    Number(stamp.slice(6, 8)),
    Number(stamp.slice(9, 11)),
    Number(stamp.slice(11, 13)),
  );
}

export async function getNysmTimeseries(
  dataset: "nysm" | "nyc",
  stations: string[],
  variables: string[],
  start: number,
  end: number,
): Promise<MesonetObservation[]> {
  const payload = await fetchJson<TimeseriesResponse>(NYSM_TIMESERIES_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dataset, variables, stations, start: isoMinute(start), end: isoMinute(end) }),
    timeoutMs: 20_000,
    retries: 2,
  });
  if (!payload.success) throw new Error(`NYS Mesonet ${dataset} request failed`);
  const stationIds = payload.response.coords.station.data;
  const times = payload.response.coords.time.data.map(parseStamp);
  const rows = new Map<string, MesonetObservation>();
  const rowFor = (station: string, time: number) => {
    const key = `${station}|${time}`;
    let row = rows.get(key);
    if (!row) {
      row = { station, time, tair: null };
      rows.set(key, row);
    }
    return row;
  };
  for (const [name, variable] of Object.entries(payload.response.data_vars)) {
    const target = VARIABLE_MAP[name];
    if (!target) continue;
    const scale = variable.attrs?.scale_factor ?? 1;
    // The API returns either [station][time] or [time][station] depending on the request.
    const stationFirst = variable.dims[0] === "station";
    times.forEach((time, ti) => {
      stationIds.forEach((station, si) => {
        const raw = stationFirst ? variable.data[si]?.[ti] : variable.data[ti]?.[si];
        if (raw === null || raw === undefined) return;
        const value = raw * scale;
        const row = rowFor(station, time) as Record<string, unknown>;
        row[target] = target === "tair" ? (value * 9) / 5 + 32 : value;
      });
    });
  }
  return [...rows.values()].sort((a, b) => a.time - b.time);
}
