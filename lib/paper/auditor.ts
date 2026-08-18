import type { KalshiBookSnapshot } from "./orderbook";

export type CompatibilityStatus = "unverified" | "proven" | "invalid";
export type AuditStatus = "approved" | "blocked" | "shadow_only";

export type SignalAuditInput = {
  strategyCode: string;
  hardStateRequired: boolean;
  compatibilityStatus: CompatibilityStatus;
  compatibilityRule?: string | null;
  observedAtMs: number;
  firstSeenAtMs: number;
  nowMs: number;
  book: KalshiBookSnapshot | null;
  maxBookAgeMs: number;
  sourceClockFutureToleranceMs?: number;
};

export type AuditVerdict = {
  status: AuditStatus;
  reasons: string[];
  metrics: Record<string, number | string | boolean | null>;
};

export function auditSignal(input: SignalAuditInput): AuditVerdict {
  const reasons: string[] = [];
  const futureTolerance = input.sourceClockFutureToleranceMs ?? 2_000;
  const sourceAgeMs = input.nowMs - input.firstSeenAtMs;
  const observationLagMs = input.firstSeenAtMs - input.observedAtMs;
  const bookAgeMs = input.book ? input.nowMs - input.book.receivedEpochMs : Number.POSITIVE_INFINITY;

  if (input.observedAtMs > input.firstSeenAtMs + futureTolerance) reasons.push("WEATHER_TIMESTAMP_IN_FUTURE");
  if (input.firstSeenAtMs > input.nowMs + futureTolerance) reasons.push("FIRST_SEEN_TIMESTAMP_IN_FUTURE");
  if (!input.book) reasons.push("NO_L2_BOOK");
  if (input.book && bookAgeMs > input.maxBookAgeMs) reasons.push("STALE_L2_BOOK");
  if (input.hardStateRequired && input.compatibilityStatus !== "proven") reasons.push("SETTLEMENT_COMPATIBILITY_NOT_PROVEN");
  if (input.compatibilityStatus === "invalid") reasons.push("SOURCE_MARKED_INCOMPATIBLE");

  if (input.book) {
    const yesBid = input.book.yesBids.at(-1)?.price ?? null;
    const noBid = input.book.noBids.at(-1)?.price ?? null;
    if (yesBid !== null && noBid !== null && yesBid + noBid > 1.000001) reasons.push("CROSSED_OR_CORRUPT_BINARY_BOOK");
    if (input.book.seq <= 0) reasons.push("BOOK_SEQUENCE_UNAVAILABLE");
  }

  let status: AuditStatus = "approved";
  if (reasons.includes("SOURCE_MARKED_INCOMPATIBLE") || reasons.includes("NO_L2_BOOK") || reasons.includes("WEATHER_TIMESTAMP_IN_FUTURE") || reasons.includes("CROSSED_OR_CORRUPT_BINARY_BOOK")) {
    status = "blocked";
  } else if (reasons.length) {
    status = "shadow_only";
  }

  return {
    status,
    reasons,
    metrics: {
      sourceAgeMs,
      observationLagMs,
      bookAgeMs: Number.isFinite(bookAgeMs) ? bookAgeMs : null,
      bookSeq: input.book?.seq ?? null,
      strategyCode: input.strategyCode,
      compatibilityStatus: input.compatibilityStatus,
      compatibilityRule: input.compatibilityRule ?? null,
    },
  };
}

export type ReadinessInput = {
  websocketL2: boolean;
  rawJournal: boolean;
  deterministicReplay: boolean;
  feeModelVerified: boolean;
  settlementAlgorithmVerified: boolean;
  weatherSourceLiveTimestamped: boolean;
  sequenceGapRecovery: boolean;
  independentAuditLedger: boolean;
  clockSkewMonitored: boolean;
};

export function liveMoneyReadiness(input: ReadinessInput) {
  const required = Object.entries(input);
  const failed = required.filter(([, ok]) => !ok).map(([name]) => name);
  return {
    ready: failed.length === 0,
    failed,
    score: Math.round(((required.length - failed.length) / required.length) * 100),
  };
}
