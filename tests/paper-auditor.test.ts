import assert from "node:assert/strict";
import test from "node:test";
import { auditSignal, liveMoneyReadiness } from "../lib/paper/auditor";
import type { KalshiBookSnapshot } from "../lib/paper/orderbook";

const book: KalshiBookSnapshot = {
  marketTicker: "WX",
  seq: 55,
  exchangeTsMs: 9_990,
  receivedEpochMs: 10_000,
  yesBids: [{ price: 0.67, qty: 10 }],
  noBids: [{ price: 0.31, qty: 10 }],
};

test("hard-state strategy is shadow-only until settlement compatibility is proven", () => {
  const verdict = auditSignal({
    strategyCode: "DBN",
    hardStateRequired: true,
    compatibilityStatus: "unverified",
    observedAtMs: 9_000,
    firstSeenAtMs: 9_500,
    nowMs: 10_010,
    book,
    maxBookAgeMs: 100,
  });
  assert.equal(verdict.status, "shadow_only");
  assert.ok(verdict.reasons.includes("SETTLEMENT_COMPATIBILITY_NOT_PROVEN"));
});

test("hard-state strategy approves only with proven compatibility and fresh sequenced book", () => {
  const verdict = auditSignal({
    strategyCode: "DBN",
    hardStateRequired: true,
    compatibilityStatus: "proven",
    compatibilityRule: "rule-v1",
    observedAtMs: 9_000,
    firstSeenAtMs: 9_500,
    nowMs: 10_010,
    book,
    maxBookAgeMs: 100,
  });
  assert.equal(verdict.status, "approved");
  assert.deepEqual(verdict.reasons, []);
});

test("missing L2 or impossible timestamps block the signal", () => {
  const noBook = auditSignal({
    strategyCode: "DBN",
    hardStateRequired: true,
    compatibilityStatus: "proven",
    observedAtMs: 11_000,
    firstSeenAtMs: 9_500,
    nowMs: 10_000,
    book: null,
    maxBookAgeMs: 100,
    sourceClockFutureToleranceMs: 100,
  });
  assert.equal(noBook.status, "blocked");
  assert.ok(noBook.reasons.includes("NO_L2_BOOK"));
  assert.ok(noBook.reasons.includes("WEATHER_TIMESTAMP_IN_FUTURE"));
});

test("readiness gate is all-or-nothing and names every missing layer", () => {
  const result = liveMoneyReadiness({
    websocketL2: true,
    rawJournal: true,
    deterministicReplay: true,
    feeModelVerified: true,
    settlementAlgorithmVerified: false,
    weatherSourceLiveTimestamped: false,
    sequenceGapRecovery: true,
    independentAuditLedger: true,
    clockSkewMonitored: false,
  });
  assert.equal(result.ready, false);
  assert.deepEqual(result.failed.sort(), ["clockSkewMonitored", "settlementAlgorithmVerified", "weatherSourceLiveTimestamped"].sort());
});
