import { AWC_BASE_URL } from "../config";
import { fetchJson } from "../http";
import type { ObservationPoint } from "../types";

type AwcReport = {
  icaoId: string;
  receiptTime: string;
  obsTime: number;
  temp?: number;
  dewp?: number;
  metarType?: "METAR" | "SPECI";
  rawOb: string;
  [key: string]: unknown;
};

export type DetailedObservation = ObservationPoint & {
  dewpointF: number | null;
  windDirection: number | string | null;
  windSpeedKt: number | null;
  pressureHpa: number | null;
  skyCover: string | null;
  precipitationIn: number | null;
  payload: AwcReport;
};

const cToF = (celsius: number) => (celsius * 9) / 5 + 32;

export async function getMetars(
  station: string,
  asOf: Date,
  hours: number,
): Promise<DetailedObservation[]> {
  const date = asOf.toISOString().replace(/[-:]/g, "").slice(0, 8) + "_" + asOf.toISOString().slice(11, 16).replace(":", "");
  const params = new URLSearchParams({ ids: station, format: "json", date, hours: String(hours) });
  const reports = await fetchJson<AwcReport[]>(`${AWC_BASE_URL}/metar?${params}`);

  return reports
    .filter((report) => typeof report.temp === "number")
    .map((report) => ({
      station,
      source: "NOAA_AWC",
      reportType: report.metarType ?? "METAR",
      observedAt: new Date(report.obsTime * 1000).toISOString(),
      receivedAt: new Date(report.receiptTime).toISOString(),
      receiptQuality: "actual",
      temperatureF: cToF(report.temp as number),
      // AWC is an important upstream observation source, but a METAR current
      // temperature is NOT automatically identical to the value/method used by
      // the event's versioned settlement source. Hard-state strategies must earn
      // compatibility through a separately audited settlement transformation.
      settlementCompatible: false,
      rawText: report.rawOb,
      dewpointF: typeof report.dewp === "number" ? cToF(report.dewp) : null,
      windDirection: (report.wdir as number | string | undefined) ?? null,
      windSpeedKt: typeof report.wspd === "number" ? report.wspd : null,
      pressureHpa: typeof report.altim === "number" ? report.altim : null,
      skyCover: typeof report.cover === "string" ? report.cover : null,
      precipitationIn: typeof report.precip === "number" ? report.precip : null,
      payload: report,
    }));
}
