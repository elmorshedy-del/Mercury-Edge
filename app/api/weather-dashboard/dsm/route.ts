import { NextResponse } from "next/server";
import { NWS_BASE_URL, STATIONS } from "@/lib/config";
import { fetchJson } from "@/lib/http";

export const dynamic = "force-dynamic";

const DASHBOARD_STATIONS = new Set(["KNYC", "KPHL", "KLAX", "KDEN", "KSEA"]);
const HISTORY_LIMIT = 8;
const INDEX_REFRESH_MS = 15_000;

const STANDARD_OFFSETS: Record<string, number> = {
  "America/New_York": -5,
  "America/Chicago": -6,
  "America/Denver": -7,
  "America/Los_Angeles": -8,
  "America/Phoenix": -7,
};

type ProductIndex = {
  "@graph": Array<{
    id: string;
    issuanceTime: string;
    productCode: string;
    productName: string;
  }>;
};

type ProductDetail = {
  id: string;
  issuanceTime: string;
  productCode: string;
  productName: string;
  productText: string;
  issuingOffice: string;
  [key: string]: unknown;
};

type DsmRelease = {
  productId: string;
  issuedAt: string;
  summaryDate: string | null;
  cycle: string | null;
  reportedHighF: number | null;
  highObservedAt: string | null;
  highObservedClock: string | null;
  rawText: string;
  sourceUrl: string;
};

type StationDsmCache = {
  checkedAt: number;
  releases: DsmRelease[];
};

declare global {
  var weatherDashboardDsmMemory: Map<string, StationDsmCache> | undefined;
}

const dsmMemory = global.weatherDashboardDsmMemory ?? new Map<string, StationDsmCache>();
if (!global.weatherDashboardDsmMemory) global.weatherDashboardDsmMemory = dsmMemory;

function parseSignedTemperature(token: string) {
  if (/^M\d+$/.test(token)) return -Number(token.slice(1));
  const value = Number(token);
  return Number.isFinite(value) ? value : null;
}

function parseDashboardDsm(text: string, issuedAt: string, timezone: string) {
  // Operational DSMs appear in both forms:
  //   KPHL DS 0800 11/08 800759/ ...  (cycle token present)
  //   KNYC DS 20/08 841055/ ...       (final/backup form, no cycle token)
  // Keep this compatibility parser local to the dashboard so Mercury's shared
  // ingestion/parser behavior is not changed.
  const match = text.match(
    /\bK[A-Z0-9]{3}\s+DS(?:\s+(\d{4}))?\s+(\d{2})\/(\d{2})\s+(M?\d{2,3})(\d{4})\//,
  );
  if (!match) {
    return {
      summaryDate: null,
      cycle: null,
      high: null,
      highAt: null,
      highClock: null,
    };
  }

  const issued = new Date(issuedAt);
  const day = Number(match[2]);
  const month = Number(match[3]);
  let year = issued.getUTCFullYear();
  if (issued.getUTCMonth() === 0 && month === 12) year -= 1;

  const high = parseSignedTemperature(match[4]);
  const highToken = match[5];
  const hour = Number(highToken.slice(0, 2));
  const minute = Number(highToken.slice(2));
  const offset = STANDARD_OFFSETS[timezone];
  const highAt = offset === undefined
    ? null
    : new Date(Date.UTC(year, month - 1, day, hour - offset, minute)).toISOString();

  return {
    summaryDate: `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`,
    cycle: match[1] ?? null,
    high,
    highAt,
    highClock: `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")} LST`,
  };
}

function normalizeProduct(detail: ProductDetail, timezone: string): DsmRelease {
  const parsed = parseDashboardDsm(detail.productText, detail.issuanceTime, timezone);
  return {
    productId: detail.id,
    issuedAt: new Date(detail.issuanceTime).toISOString(),
    summaryDate: parsed.summaryDate,
    cycle: parsed.cycle,
    reportedHighF: parsed.high,
    highObservedAt: parsed.highAt,
    highObservedClock: parsed.highClock,
    rawText: detail.productText,
    sourceUrl: `${NWS_BASE_URL}/products/${detail.id}`,
  };
}

async function fetchStationDsm(station: (typeof STATIONS)[number]) {
  if (!station.nwsLocation) return [] as DsmRelease[];

  const now = Date.now();
  const cached = dsmMemory.get(station.station);
  if (cached && now - cached.checkedAt < INDEX_REFRESH_MS) return cached.releases;

  const indexUrl = `${NWS_BASE_URL}/products/types/DSM/locations/${station.nwsLocation}`;
  const index = await fetchJson<ProductIndex>(indexUrl, { timeoutMs: 8_000, retries: 0 });
  const items = index["@graph"].slice(0, HISTORY_LIMIT);
  const known = new Map((cached?.releases ?? []).map((release) => [release.productId, release]));
  const missing = items.filter((item) => !known.has(item.id));

  const fresh = await Promise.all(
    missing.map(async (item) => {
      const detail = await fetchJson<ProductDetail>(`${NWS_BASE_URL}/products/${item.id}`, {
        timeoutMs: 8_000,
        retries: 0,
      });
      return normalizeProduct(detail, station.timezone);
    }),
  );

  for (const release of fresh) known.set(release.productId, release);
  const releases = items
    .map((item) => known.get(item.id))
    .filter((release): release is DsmRelease => Boolean(release));

  dsmMemory.set(station.station, { checkedAt: now, releases });
  return releases;
}

export async function GET() {
  const configs = STATIONS.filter(
    (station) => DASHBOARD_STATIONS.has(station.station) && Boolean(station.nwsLocation),
  );

  const stations = await Promise.all(
    configs.map(async (station) => {
      try {
        const releases = await fetchStationDsm(station);
        return {
          stid: station.station,
          city: station.city,
          timezone: station.timezone,
          nwsLocation: station.nwsLocation,
          releases,
          error: null,
        };
      } catch (error) {
        console.error(`DSM dashboard fetch failed for ${station.station}`, error);
        return {
          stid: station.station,
          city: station.city,
          timezone: station.timezone,
          nwsLocation: station.nwsLocation,
          releases: dsmMemory.get(station.station)?.releases ?? [],
          error: error instanceof Error ? error.message : "DSM fetch failed",
        };
      }
    }),
  );

  return NextResponse.json(
    {
      updatedAt: new Date().toISOString(),
      source: "NWS DSM products",
      pollFloorMs: INDEX_REFRESH_MS,
      stations,
    },
    { headers: { "Cache-Control": "no-store, max-age=0, must-revalidate" } },
  );
}
