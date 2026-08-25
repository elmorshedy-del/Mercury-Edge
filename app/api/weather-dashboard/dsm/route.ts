import { NextResponse } from "next/server";
import { NWS_BASE_URL, STATIONS } from "@/lib/config";
import { fetchJson } from "@/lib/http";
import { parseDsm } from "@/lib/sources/nws";

export const dynamic = "force-dynamic";

const DASHBOARD_STATIONS = new Set(["KNYC", "KPHL", "KLAX", "KDEN", "KSEA"]);
const HISTORY_LIMIT = 8;
const INDEX_REFRESH_MS = 8_000;

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
  reportedHighF: number | null;
  highObservedAt: string | null;
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

function normalizeProduct(detail: ProductDetail, timezone: string): DsmRelease {
  const parsed = parseDsm(detail.productText, detail.issuanceTime, timezone);
  return {
    productId: detail.id,
    issuedAt: new Date(detail.issuanceTime).toISOString(),
    reportedHighF: parsed.high,
    highObservedAt: parsed.highAt,
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
