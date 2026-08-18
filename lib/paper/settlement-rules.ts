import { createHash } from "node:crypto";
import { KALSHI_BASE_URL } from "../config";
import { fetchJson } from "../http";

export type SettlementSource = { name: string; url: string };

export type SeriesRuleSnapshot = {
  seriesTicker: string;
  capturedAt: string;
  settlementSources: SettlementSource[];
  contractTermsUrl: string | null;
  contractUrl: string | null;
  feeType: string | null;
  feeMultiplier: number | null;
  importantInfo: unknown;
  rawPayload: unknown;
  rulesHash: string;
};

function sha(value: unknown) {
  return createHash("sha256").update(JSON.stringify(value)).digest("hex");
}

export async function getSeriesRuleSnapshot(seriesTicker: string): Promise<SeriesRuleSnapshot> {
  const payload = await fetchJson<{ series: Record<string, unknown> }>(
    `${KALSHI_BASE_URL}/series/${encodeURIComponent(seriesTicker)}`,
  );
  const series = payload.series;
  const sourceList = Array.isArray(series.settlement_sources) ? series.settlement_sources : [];
  const settlementSources = sourceList
    .filter((x): x is Record<string, unknown> => Boolean(x && typeof x === "object"))
    .map((x) => ({ name: String(x.name ?? ""), url: String(x.url ?? "") }));
  const importantInfo = (series.product_metadata as Record<string, unknown> | undefined)?.important_info ?? null;
  const material = {
    ticker: series.ticker,
    settlementSources,
    contract_terms_url: series.contract_terms_url ?? null,
    contract_url: series.contract_url ?? null,
    fee_type: series.fee_type ?? null,
    fee_multiplier: series.fee_multiplier ?? null,
    importantInfo,
    last_updated_ts: series.last_updated_ts ?? null,
  };
  return {
    seriesTicker,
    capturedAt: new Date().toISOString(),
    settlementSources,
    contractTermsUrl: typeof series.contract_terms_url === "string" ? series.contract_terms_url : null,
    contractUrl: typeof series.contract_url === "string" ? series.contract_url : null,
    feeType: typeof series.fee_type === "string" ? series.fee_type : null,
    feeMultiplier: typeof series.fee_multiplier === "number" ? series.fee_multiplier : Number(series.fee_multiplier ?? NaN) || null,
    importantInfo,
    rawPayload: payload,
    rulesHash: sha(material),
  };
}

export type EventRuleSnapshot = {
  eventTicker: string;
  seriesTicker: string;
  capturedAt: string;
  settlementSources: SettlementSource[];
  mutuallyExclusive: boolean | null;
  marketTickers: string[];
  rawPayload: unknown;
  rulesHash: string;
};

export async function getEventRuleSnapshot(eventTicker: string): Promise<EventRuleSnapshot> {
  const payload = await fetchJson<{ event: Record<string, unknown> }>(
    `${KALSHI_BASE_URL}/events/${encodeURIComponent(eventTicker)}?with_nested_markets=true`,
  );
  const event = payload.event;
  const sources = Array.isArray(event.settlement_sources) ? event.settlement_sources : [];
  const settlementSources = sources
    .filter((x): x is Record<string, unknown> => Boolean(x && typeof x === "object"))
    .map((x) => ({ name: String(x.name ?? ""), url: String(x.url ?? "") }));
  const markets = Array.isArray(event.markets) ? event.markets : [];
  const marketTickers = markets
    .filter((x): x is Record<string, unknown> => Boolean(x && typeof x === "object"))
    .map((x) => String(x.ticker ?? ""))
    .filter(Boolean);
  const material = {
    event_ticker: event.event_ticker,
    series_ticker: event.series_ticker,
    settlementSources,
    mutually_exclusive: event.mutually_exclusive ?? null,
    markets: marketTickers,
  };
  return {
    eventTicker,
    seriesTicker: String(event.series_ticker ?? ""),
    capturedAt: new Date().toISOString(),
    settlementSources,
    mutuallyExclusive: typeof event.mutually_exclusive === "boolean" ? event.mutually_exclusive : null,
    marketTickers,
    rawPayload: payload,
    rulesHash: sha(material),
  };
}

export function compatibilityKey(input: {
  seriesRulesHash: string;
  eventRulesHash?: string | null;
  weatherSource: string;
  reportType: string;
  transformationVersion: string;
}) {
  return sha(input);
}
