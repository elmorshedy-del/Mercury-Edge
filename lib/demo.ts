import { STATIONS } from "./config";
import type { CaseStudy, CitySummary, DashboardPayload } from "./types";

// These are deliberately narrow, source-linked case studies—not claimed to be a
// representative backtest. Production aggregates replace them once Postgres is
// configured and a backtest has been run.
export const VERIFIED_CASES: CaseStudy[] = [
  {
    id: "KPHL_2026-08-02_market-repricing",
    date: "2026-08-02",
    city: "Philadelphia",
    station: "KPHL",
    edgeClass: "market_repricing",
    headline: "A three-minute candidate repricing window after the 91°F high",
    evidenceQuality: "medium",
    observedAt: "2026-08-02T19:06:00.000Z",
    availableAt: "2026-08-02T19:06:00.000Z",
    repricedAt: "2026-08-02T19:09:00.000Z",
    observationToAvailabilitySeconds: 0,
    availabilityToRepriceSeconds: 180,
    totalLatencySeconds: 180,
    contract: "89–90°F (NO)",
    entryCents: 34,
    exitOrSettlementCents: 100,
    grossProfitCents: 66,
    finalHighF: 91,
    note: "The market candle move is visible, but the archive does not preserve the original high-frequency source receipt timestamp. Treat this as a candidate executable edge, not a certified fill.",
    sources: [
      {
        label: "NWS/IEM daily summary message",
        url: "https://mesonet.agron.iastate.edu/p.php?pid=202608022116-KZNY-CDUS27-DSMPHL",
      },
      {
        label: "Kalshi event",
        url: "https://api.elections.kalshi.com/trade-api/v2/events/KXHIGHPHIL-26AUG02?with_nested_markets=true",
      },
    ],
    executableProxy: false,
    limitation: "Original source receipt and order-book queue position are unavailable.",
  },
  {
    id: "KPHL_2026-08-05_trajectory",
    date: "2026-08-05",
    city: "Philadelphia",
    station: "KPHL",
    edgeClass: "trajectory_mispricing",
    headline: "84–85°F traded near 20¢ after a 84.92°F SPECI",
    evidenceQuality: "high",
    observedAt: "2026-08-05T16:24:00.000Z",
    availableAt: "2026-08-05T16:28:03.278Z",
    repricedAt: null,
    observationToAvailabilitySeconds: 243,
    availabilityToRepriceSeconds: null,
    totalLatencySeconds: null,
    contract: "84–85°F (YES)",
    entryCents: 20,
    exitOrSettlementCents: 100,
    grossProfitCents: 80,
    finalHighF: 85,
    note: "This is probabilistic trajectory mispricing, not deterministic invalidation: the official whole-degree outcome could still have finished above the bracket.",
    sources: [
      {
        label: "Aviation Weather Center METAR archive",
        url: "https://aviationweather.gov/data/api/",
      },
      {
        label: "Kalshi event",
        url: "https://api.elections.kalshi.com/trade-api/v2/events/KXHIGHPHIL-26AUG05?with_nested_markets=true",
      },
    ],
    executableProxy: true,
    limitation: "Minute candles do not prove displayed size, queue position, or a fill at the quoted price.",
  },
  {
    id: "KNYC_2026-08-13_publication",
    date: "2026-08-13",
    city: "New York",
    station: "KNYC",
    edgeClass: "publication_latency",
    headline: "The 85°F official high surfaced 38 minutes after occurrence",
    evidenceQuality: "high",
    observedAt: "2026-08-13T20:37:00.000Z",
    availableAt: "2026-08-13T21:15:00.000Z",
    repricedAt: "2026-08-13T21:17:04.000Z",
    observationToAvailabilitySeconds: 2280,
    availabilityToRepriceSeconds: 124,
    totalLatencySeconds: 2404,
    contract: "85–86°F",
    entryCents: null,
    exitOrSettlementCents: null,
    grossProfitCents: null,
    finalHighF: 85,
    note: "This separates publication latency from market reaction: the hidden high occurred at 4:37 PM EDT, appeared in the 5:15 PM DSM, then the market reacted about two minutes later.",
    sources: [
      {
        label: "NWS DSM product",
        url: "https://api.weather.gov/products/e2413736-9596-4bf6-b337-a80d1c9c34d4",
      },
      {
        label: "NOAA MADIS one-minute observations",
        url: "https://madis.ncep.noaa.gov/madis_OMO.shtml",
      },
    ],
    executableProxy: false,
    limitation: "No pre-publication trade is claimed; this case measures the two publication clocks.",
  },
];

function median(values: number[]) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

function latencyBucket(seconds: number) {
  if (seconds <= 60) return "≤1m";
  if (seconds <= 180) return "1–3m";
  if (seconds <= 300) return "3–5m";
  if (seconds <= 900) return "5–15m";
  return ">15m";
}

export function demoDashboard(): DashboardPayload {
  const reactionLags = VERIFIED_CASES.flatMap((item) =>
    item.availabilityToRepriceSeconds === null ? [] : [item.availabilityToRepriceSeconds],
  );
  const byStation = new Map<string, CaseStudy[]>();
  for (const item of VERIFIED_CASES) {
    byStation.set(item.station, [...(byStation.get(item.station) ?? []), item]);
  }

  const cities: CitySummary[] = STATIONS.map((station) => {
    const cases = byStation.get(station.station) ?? [];
    const executable = cases.filter((item) => item.executableProxy && item.grossProfitCents !== null);
    return {
      city: station.city,
      station: station.station,
      cases: cases.length,
      medianLagSeconds: median(cases.flatMap((item) =>
        item.availabilityToRepriceSeconds === null ? [] : [item.availabilityToRepriceSeconds],
      )),
      winRate: executable.length ? 1 : null,
      avgGrossProfitCents: executable.length
        ? executable.reduce((sum, item) => sum + (item.grossProfitCents ?? 0), 0) / executable.length
        : null,
      coverage: cases.length ? "partial" : "pending",
    };
  });

  const labels = ["≤1m", "1–3m", "3–5m", "5–15m", ">15m"];
  const latencyBuckets = labels.map((label) => ({
    label,
    count: reactionLags.filter((seconds) => latencyBucket(seconds) === label).length,
  }));

  return {
    mode: "verified_demo",
    generatedAt: new Date().toISOString(),
    headline: {
      verifiedCases: VERIFIED_CASES.filter((item) => item.evidenceQuality === "high").length,
      candidateCases: VERIFIED_CASES.filter((item) => item.evidenceQuality !== "high").length,
      citiesCovered: STATIONS.length,
      medianMarketReactionSeconds: median(reactionLags),
      grossProfitCents: VERIFIED_CASES.reduce((sum, item) => sum + (item.grossProfitCents ?? 0), 0),
    },
    cases: VERIFIED_CASES,
    cities,
    latencyBuckets,
    sourceHealth: [
      { name: "NOAA AWC", purpose: "METAR/SPECI + actual receipt clocks", status: "healthy", lastSeen: "2026-08-13T21:51:00.000Z" },
      { name: "NWS products", purpose: "DSM/CLI official-high releases", status: "healthy", lastSeen: "2026-08-13T21:15:00.000Z" },
      { name: "NOAA MADIS", purpose: "One-minute ASOS evidence", status: "unconfigured", lastSeen: null },
      { name: "Kalshi", purpose: "Events, contracts, minute candles", status: "healthy", lastSeen: "2026-08-13T21:18:00.000Z" },
    ],
  };
}
