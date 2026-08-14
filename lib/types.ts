export type EdgeClass =
  | "market_repricing"
  | "publication_latency"
  | "trajectory_mispricing";

export type EvidenceQuality = "high" | "medium" | "low";

export type ReceiptQuality = "actual" | "bounded" | "discovery_only";

export type ContractBand = {
  ticker: string;
  label: string;
  lower: number | null;
  upper: number | null;
  result?: "yes" | "no" | "unknown";
};

export type ObservationPoint = {
  station: string;
  source: string;
  reportType: "METAR" | "SPECI" | "OMO" | "DSM" | "CLI" | "OTHER";
  observedAt: string;
  receivedAt: string;
  receiptQuality: ReceiptQuality;
  temperatureF: number;
  settlementCompatible: boolean;
  rawText?: string;
};

export type QuotePoint = {
  contractTicker: string;
  capturedAt: string;
  yesBid: number | null;
  yesAsk: number | null;
  lastPrice?: number | null;
  bidSize?: number | null;
  askSize?: number | null;
};

export type MechanicalSignal = {
  station: string;
  contractTicker: string;
  triggeredAt: string;
  triggerObservedAt: string;
  runningHighF: number;
  officialFloorF: number;
  edgeClass: "market_repricing";
  action: "BUY_NO";
  entryPrice: number | null;
  quoteCapturedAt: string | null;
  reactionAt: string | null;
  reactionLagSeconds: number | null;
  grossProfitPerContract: number | null;
  executableProxy: boolean;
  limitation: string | null;
};

export type CaseStudy = {
  id: string;
  date: string;
  city: string;
  station: string;
  edgeClass: EdgeClass;
  headline: string;
  evidenceQuality: EvidenceQuality;
  observedAt: string;
  availableAt: string;
  repricedAt: string | null;
  observationToAvailabilitySeconds: number;
  availabilityToRepriceSeconds: number | null;
  totalLatencySeconds: number | null;
  contract: string;
  entryCents: number | null;
  exitOrSettlementCents: number | null;
  grossProfitCents: number | null;
  finalHighF: number | null;
  note: string;
  sources: Array<{ label: string; url: string }>;
  executableProxy: boolean;
  limitation: string;
};

export type CitySummary = {
  city: string;
  station: string;
  cases: number;
  medianLagSeconds: number | null;
  winRate: number | null;
  avgGrossProfitCents: number | null;
  coverage: "live" | "partial" | "pending";
};

export type DashboardPayload = {
  mode: "database" | "verified_demo";
  generatedAt: string;
  headline: {
    verifiedCases: number;
    candidateCases: number;
    citiesCovered: number;
    medianMarketReactionSeconds: number | null;
    grossProfitCents: number;
  };
  cases: CaseStudy[];
  cities: CitySummary[];
  latencyBuckets: Array<{ label: string; count: number }>;
  sourceHealth: Array<{
    name: string;
    purpose: string;
    status: "healthy" | "stale" | "unconfigured";
    lastSeen: string | null;
  }>;
};
