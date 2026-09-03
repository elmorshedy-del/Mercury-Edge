export type EdgeClass =
  | "market_repricing"
  | "publication_latency"
  | "trajectory_mispricing";

export type EvidenceQuality = "high" | "medium" | "low";

export type ReceiptQuality = "actual" | "bounded" | "discovery_only";

export type HardTriggerKind =
  | "metar_current_temp"
  | "metar_6h_max"
  | "metar_24h_max"
  | "speci_current_temp"
  | "omo_precise_temp"
  | "normalized_current_temp";

export type BacktestEvidenceTier =
  | "l2_simulated"
  | "trade_tape_observed"
  | "minute_candle_proxy"
  | "weather_only";

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
  maxTemperatureF?: number | null;
  maxTemperatureKind?: Extract<HardTriggerKind, "metar_6h_max" | "metar_24h_max"> | null;
  settlementCompatible: boolean;
  rawText?: string;
  payload?: Record<string, unknown>;
};

export type QuotePoint = {
  contractTicker: string;
  capturedAt: string;
  yesBid: number | null;
  yesAsk: number | null;
  yesBidOpen?: number | null;
  yesBidLow?: number | null;
  yesBidHigh?: number | null;
  yesAskOpen?: number | null;
  yesAskLow?: number | null;
  yesAskHigh?: number | null;
  lastPrice?: number | null;
  lastPriceOpen?: number | null;
  lastPriceLow?: number | null;
  lastPriceHigh?: number | null;
  bidSize?: number | null;
  askSize?: number | null;
  sourcePrecision?: "minute_candle" | "l2";
};

export type MarketTrade = {
  tradeId: string;
  contractTicker: string;
  createdAt: string;
  yesPrice: number;
  noPrice: number;
  quantity: number;
  takerOutcomeSide: "yes" | "no" | null;
  takerBookSide: "bid" | "ask" | null;
  isBlockTrade: boolean;
};

export type MechanicalSignal = {
  station: string;
  contractTicker: string;
  triggeredAt: string;
  triggerObservedAt: string;
  alignmentClock: "source_receipt" | "observation_time_only";
  triggerSource: string;
  triggerReportType: ObservationPoint["reportType"];
  triggerKind: HardTriggerKind;
  triggerReceiptQuality: ReceiptQuality;
  triggerRawText: string | null;
  runningHighF: number;
  officialFloorF: number;
  edgeClass: "market_repricing";
  action: "BUY_NO";
  entryPrice: number | null;
  quoteCapturedAt: string | null;
  reactionAt: string | null;
  reactionLagSeconds: number | null;
  reactionLagLowerSeconds: number | null;
  reactionLagUpperSeconds: number | null;
  preTriggerYesBid: number | null;
  preTriggerYesMid: number | null;
  postWindowLowYesBid: number | null;
  violentMoveCents: number | null;
  violent: boolean;
  staleEdgeCents: number | null;
  candidateProxy: boolean;
  hardStateProven: boolean;
  evidenceTier: BacktestEvidenceTier;
  observedNoTakerQuantity: number;
  observedNoTakerVwap: number | null;
  tapeCounterfactualNetProfit: number | null;
  profitStatus: "l2_simulated" | "counterfactual_tape" | "hypothetical_one_contract" | "none";
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
