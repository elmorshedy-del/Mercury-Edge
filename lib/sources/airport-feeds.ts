import { AWC_BASE_URL, NWS_BASE_URL } from "../config";
import { fetchJson } from "../http";
import type { AirportHfObservation, AirportMetar } from "../weather/manh-nowcast";

// Live feeds for the airport neighbours used by the MANH -> KNYC nowcast.
// api.weather.gov serves the 5-minute ASOS (MADIS HF) reports reading-for-reading identical to
// the IEM archive used in training (whole degrees C, wind in km/h), typically 12-30 minutes late.

type NwsObservation = {
  properties: {
    timestamp: string;
    rawMessage?: string | null;
    temperature?: { value: number | null };
    windDirection?: { value: number | null };
    windSpeed?: { value: number | null };
  };
};

export async function getAirportHf(stations: string[], start: number): Promise<AirportHfObservation[]> {
  const iso = new Date(start).toISOString().replace(/\.\d{3}Z$/, "Z");
  const results = await Promise.all(
    stations.map(async (code) => {
      try {
        const payload = await fetchJson<{ features: NwsObservation[] }>(
          `${NWS_BASE_URL}/stations/K${code}/observations?start=${iso}&limit=500`,
          { timeoutMs: 15_000, retries: 1 },
        );
        return payload.features.flatMap((feature): AirportHfObservation[] => {
          const p = feature.properties;
          const time = Date.parse(p.timestamp);
          const date = new Date(time);
          // HF rows carry no raw METAR text and sit on the 5-minute grid.
          if (p.rawMessage || date.getUTCMinutes() % 5 !== 0 || date.getUTCSeconds() !== 0) return [];
          const c = p.temperature?.value;
          const kmh = p.windSpeed?.value;
          const dir = p.windDirection?.value;
          return [{
            station: code,
            time,
            tempF: typeof c === "number" ? (Math.round(c) * 9) / 5 + 32 : null,
            dirDeg: typeof dir === "number" ? dir : null,
            speedKt: typeof kmh === "number" ? Math.round(kmh / 1.852) : null,
          }];
        });
      } catch {
        return [];
      }
    }),
  );
  return results.flat();
}

type AwcReport = { icaoId: string; obsTime: number; rawOb: string };

export async function getAirportMetars(stations: string[], hours: number): Promise<AirportMetar[]> {
  const params = new URLSearchParams({ ids: stations.map((code) => `K${code}`).join(","), format: "json", hours: String(hours) });
  const rows = await fetchJson<AwcReport[]>(`${AWC_BASE_URL}/metar?${params}`, { retries: 1 });
  return rows.flatMap((row): AirportMetar[] => {
    const t = / T([01])(\d{3})([01])(\d{3})/.exec(row.rawOb);
    if (!t) return [];
    const celsius = (Number(t[2]) / 10) * (t[1] === "1" ? -1 : 1);
    return [{ station: row.icaoId.replace(/^K/, ""), valid: row.obsTime * 1000, temp: Math.round((celsius * 9) / 5 + 32) }];
  });
}
