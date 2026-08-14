import { IEM_BASE_URL } from "../config";
import { fetchText } from "../http";
import type { ObservationPoint } from "../types";

const cToF = (celsius: number) => (celsius * 9) / 5 + 32;

function reportDate(date: string, day: number, hour: number, minute: number) {
  const base = new Date(`${date}T00:00:00Z`);
  let year = base.getUTCFullYear();
  let month = base.getUTCMonth();
  if (day > base.getUTCDate() + 15) month -= 1;
  if (month < 0) {
    month = 11;
    year -= 1;
  }
  return new Date(Date.UTC(year, month, day, hour, minute));
}

export async function getHighFrequencyArchive(
  station: string,
  network: string,
  date: string,
  discoveredAt = new Date(),
): Promise<ObservationPoint[]> {
  const short = station.replace(/^K/, "");
  const params = new URLSearchParams({ network, station: short, date });
  const html = await fetchText(`${IEM_BASE_URL}/sites/obhistory.php?${params}`);
  const pattern = new RegExp(`(${station}\\s+(\\d{2})(\\d{2})(\\d{2})Z[^<]*?T(-?\\d{3,4})(-?\\d{3,4})\\s+MADISHF)`, "g");
  const points: ObservationPoint[] = [];

  for (const match of html.matchAll(pattern)) {
    const observed = reportDate(date, Number(match[2]), Number(match[3]), Number(match[4]));
    const tempToken = match[5];
    const sign = tempToken.startsWith("-") ? -1 : 1;
    const tempC = sign * Number(tempToken.replace("-", "")) / 10;
    points.push({
      station,
      source: "IEM_MADIS_OMO",
      reportType: "OMO",
      observedAt: observed.toISOString(),
      // Historical IEM pages do not preserve original receipt timestamps. The
      // discovery time is honest but makes the point ineligible for latency P&L.
      receivedAt: discoveredAt.toISOString(),
      receiptQuality: "discovery_only",
      temperatureF: cToF(tempC),
      settlementCompatible: true,
      rawText: match[1],
    });
  }
  return points;
}
