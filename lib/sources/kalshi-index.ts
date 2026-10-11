import { KALSHI_BASE_URL } from "../config";
import { fetchJson } from "../http";
import type { KalshiIndexFeed } from "../weather/manh-nowcast";

// Kalshi's live weather index (GET /live_data/weather/{city}). For NYC it is the mean of eight
// airports' whole-degree-C 5-minute temperatures (see research/manh_knyc/kalshi_index.py),
// served every minute ~5-6 minutes after the stamp. from/to are epoch milliseconds.

type Response = {
  city: string;
  config_version: string;
  units: string;
  timeseries: Array<{ t: number; v: number; contributors: number; status: string }>;
};

export async function getKalshiIndex(city: string, from: number, to: number): Promise<KalshiIndexFeed> {
  const params = new URLSearchParams({ from: String(from), to: String(to) });
  const payload = await fetchJson<Response>(`${KALSHI_BASE_URL}/live_data/weather/${encodeURIComponent(city)}?${params}`, {
    timeoutMs: 15_000,
    retries: 1,
  });
  return { configVersion: payload.config_version ?? "", points: payload.timeseries ?? [] };
}
