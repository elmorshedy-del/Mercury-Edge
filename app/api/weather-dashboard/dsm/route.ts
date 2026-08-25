import { NextResponse } from "next/server";
import { STATIONS } from "@/lib/config";

export const dynamic = "force-dynamic";

const DASHBOARD_STATIONS = new Set(["KNYC", "KPHL", "KLAX", "KDEN", "KSEA"]);
const DSM_PILS: Record<string, string> = {
  KNYC: "DSMNYC",
  KPHL: "DSMPHL",
  KLAX: "DSMLAX",
  KDEN: "DSMDEN",
  KSEA: "DSMSEA",
};
const IEM_AFOS_LIST_URL = "https://mesonet.agron.iastate.edu/api/1/nws/afos/list.json";
const HISTORY_LIMIT = 16;
const INDEX_REFRESH_MS = 30_000;
const USER_AGENT = process.env.SOURCE_USER_AGENT || "Mercury-Edge DSM dashboard";

// DSM clocks are Local Standard Time (LST), including during daylight time.
const STANDARD_OFFSETS: Record<string, number> = {
  "America/New_York": -5,
  "America/Chicago": -6,
  "America/Denver": -7,
  "America/Los_Angeles": -8,
  "America/Phoenix": -7,
};

type IemProductIndex = {
  data: IemProductItem[];
};

type IemProductItem = {
  entered: string;
  pil: string;
  product_id?: string | null;
  link?: string | null;
  text_link: string;
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
  transmissionCount: number;
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

function dateInTimezone(date: Date, timezone: string) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const value = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? "";
  return `${value("year")}-${value("month")}-${value("day")}`;
}

function parseDashboardDsm(text: string, issuedAt: string, timezone: string, stid: string) {
  // Operational DSMs appear in both forms:
  //   KPHL DS 0800 11/08 800759/ ...  (intraday cycle)
  //   KNYC DS 20/08 841055/ ...       (final/backup transmission)
  // This compatibility parser remains local to the temporary dashboard.
  const match = text.match(
    /\b(K[A-Z0-9]{3})\s+DS(?:\s+(\d{4}))?\s+(\d{2})\/(\d{2})\s+(M?\d{2,3})(\d{4})\//,
  );
  if (!match || match[1] !== stid) {
    return {
      summaryDate: null,
      cycle: null,
      high: null,
      highAt: null,
      highClock: null,
    };
  }

  const issued = new Date(issuedAt);
  const day = Number(match[3]);
  const month = Number(match[4]);
  let year = issued.getUTCFullYear();
  const candidate = Date.UTC(year, month - 1, day);
  const delta = candidate - issued.getTime();
  if (delta > 31 * 24 * 60 * 60 * 1000) year -= 1;
  if (delta < -335 * 24 * 60 * 60 * 1000) year += 1;

  const high = parseSignedTemperature(match[5]);
  const highToken = match[6];
  const hour = Number(highToken.slice(0, 2));
  const minute = Number(highToken.slice(2));
  const offset = STANDARD_OFFSETS[timezone];
  const highAt = offset === undefined
    ? null
    : new Date(Date.UTC(year, month - 1, day, hour - offset, minute)).toISOString();

  return {
    summaryDate: `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`,
    cycle: match[2] ?? null,
    high,
    highAt,
    highClock: `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")} LST`,
  };
}

function productId(item: IemProductItem) {
  if (item.product_id) return item.product_id;
  const fromUrl = item.text_link.split("/").filter(Boolean).at(-1);
  return fromUrl ?? `${item.pil}-${item.entered}`;
}

function normalizeProduct(
  item: IemProductItem,
  productText: string,
  station: (typeof STATIONS)[number],
): DsmRelease {
  const issuedAt = new Date(item.entered).toISOString();
  const rawText = productText.replace(/[\u0001\u0003]/g, "").trim();
  const parsed = parseDashboardDsm(rawText, issuedAt, station.timezone, station.station);
  return {
    productId: productId(item),
    issuedAt,
    summaryDate: parsed.summaryDate,
    cycle: parsed.cycle,
    reportedHighF: parsed.high,
    highObservedAt: parsed.highAt,
    highObservedClock: parsed.highClock,
    rawText,
    sourceUrl: item.link ?? item.text_link,
    transmissionCount: 1,
  };
}

function compareDsmFreshness(a: DsmRelease, b: DsmRelease) {
  return new Date(b.issuedAt).getTime() - new Date(a.issuedAt).getTime();
}

function collapseDsmRetransmissions(products: DsmRelease[]) {
  const collapsed = new Map<string, DsmRelease>();
  for (const product of [...products].sort(compareDsmFreshness)) {
    // Intraday cycles are distinct reports. Only repeated copies/corrections of
    // the same date + cycle are collapsed.
    const key = product.summaryDate
      ? `${product.summaryDate}:${product.cycle ?? "FINAL"}`
      : product.productId;
    const existing = collapsed.get(key);
    if (!existing) {
      collapsed.set(key, { ...product, transmissionCount: 1 });
    } else {
      existing.transmissionCount += 1;
    }
  }
  return [...collapsed.values()].sort(compareDsmFreshness);
}

function releasesForToday(releases: DsmRelease[], timezone: string, now = new Date()) {
  const todayDate = dateInTimezone(now, timezone);
  return collapseDsmRetransmissions(
    releases.filter((release) => release.summaryDate === todayDate),
  );
}

async function fetchWithTimeout(url: string, asText = false) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 8_000);
  try {
    const response = await fetch(url, {
      headers: {
        "User-Agent": USER_AGENT,
        Accept: asText ? "text/plain" : "application/json",
      },
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`DSM source request failed (${response.status})`);
    return asText ? response.text() : response.json();
  } finally {
    clearTimeout(timer);
  }
}

async function fetchStationDsm(station: (typeof STATIONS)[number]) {
  const pil = DSM_PILS[station.station];
  if (!pil) return [] as DsmRelease[];

  const now = new Date();
  const cached = dsmMemory.get(station.station);
  if (cached && now.getTime() - cached.checkedAt < INDEX_REFRESH_MS) {
    return releasesForToday(cached.releases, station.timezone, now);
  }

  // During the evening in the US, UTC may already be on the following date.
  // Query both relevant UTC calendar dates without pulling the entire DSM index.
  const dates = [...new Set([now.toISOString().slice(0, 10), dateInTimezone(now, station.timezone)])];
  const indexes = await Promise.all(
    dates.map(async (date) => {
      const url = new URL(IEM_AFOS_LIST_URL);
      url.searchParams.set("pil", pil);
      url.searchParams.set("date", date);
      return fetchWithTimeout(url.toString()) as Promise<IemProductIndex>;
    }),
  );

  const unique = new Map<string, IemProductItem>();
  for (const item of indexes.flatMap((index) => index.data ?? [])) {
    if (item.pil === pil) unique.set(productId(item), item);
  }
  const items = [...unique.values()]
    .sort((a, b) => new Date(b.entered).getTime() - new Date(a.entered).getTime())
    .slice(0, HISTORY_LIMIT);

  const known = new Map((cached?.releases ?? []).map((release) => [release.productId, release]));
  const missing = items.filter((item) => !known.has(productId(item)));
  const fresh = await Promise.all(
    missing.map(async (item) => {
      const text = await fetchWithTimeout(item.text_link, true) as string;
      return normalizeProduct(item, text, station);
    }),
  );

  for (const release of fresh) known.set(release.productId, release);
  const releases = items
    .map((item) => known.get(productId(item)))
    .filter((release): release is DsmRelease => Boolean(release))
    .sort(compareDsmFreshness);

  dsmMemory.set(station.station, { checkedAt: now.getTime(), releases });
  return releasesForToday(releases, station.timezone, now);
}

export async function GET() {
  const configs = STATIONS.filter((station) => DASHBOARD_STATIONS.has(station.station));

  const stations = await Promise.all(
    configs.map(async (station) => {
      const todayDate = dateInTimezone(new Date(), station.timezone);
      const productPil = DSM_PILS[station.station];
      try {
        const releases = await fetchStationDsm(station);
        return {
          stid: station.station,
          city: station.city,
          timezone: station.timezone,
          todayDate,
          productPil,
          releases,
          error: null,
        };
      } catch (error) {
        console.error(`DSM dashboard fetch failed for ${station.station}`, error);
        return {
          stid: station.station,
          city: station.city,
          timezone: station.timezone,
          todayDate,
          productPil,
          releases: releasesForToday(
            dsmMemory.get(station.station)?.releases ?? [],
            station.timezone,
          ),
          error: error instanceof Error ? error.message : "DSM fetch failed",
        };
      }
    }),
  );

  return NextResponse.json(
    {
      updatedAt: new Date().toISOString(),
      source: "NWS DSM via IEM NOAAPort",
      pollFloorMs: INDEX_REFRESH_MS,
      stations,
    },
    { headers: { "Cache-Control": "no-store, max-age=0, must-revalidate" } },
  );
}
