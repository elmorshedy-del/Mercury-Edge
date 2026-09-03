import assert from "node:assert/strict";
import test from "node:test";
import { DEFAULT_ENGINE_CONFIG, runMechanicalLatencyBacktest } from "../lib/backtest/engine";
import type { ContractBand, MarketTrade, ObservationPoint, QuotePoint } from "../lib/types";

const contracts: ContractBand[] = [
  { ticker: "LOW", label: "83° to 84°", lower: 83, upper: 84, result: "no" },
  { ticker: "HIGH", label: "85° to 86°", lower: 85, upper: 86, result: "yes" },
];

function observation(overrides: Partial<ObservationPoint> = {}): ObservationPoint {
  return {
    station: "KNYC",
    source: "NOAA_AWC",
    reportType: "SPECI",
    observedAt: "2026-08-13T20:37:00.000Z",
    receivedAt: "2026-08-13T20:38:10.000Z",
    receiptQuality: "actual",
    temperatureF: 84.92,
    settlementCompatible: true,
    ...overrides,
  };
}

function quote(
  capturedAt: string,
  yesBid = 0.68,
  yesAsk = 0.7,
  overrides: Partial<QuotePoint> = {},
): QuotePoint {
  return {
    contractTicker: "LOW",
    capturedAt,
    yesBid,
    yesAsk,
    sourcePrecision: "minute_candle",
    ...overrides,
  };
}

function trade(overrides: Partial<MarketTrade> = {}): MarketTrade {
  return {
    tradeId: "trade-1",
    contractTicker: "LOW",
    createdAt: "2026-08-13T20:38:12.000Z",
    yesPrice: 0.6,
    noPrice: 0.4,
    quantity: 10,
    takerOutcomeSide: "no",
    takerBookSide: "ask",
    isBlockTrade: false,
    ...overrides,
  };
}

test("uses the first post-receipt candle without claiming a fill", () => {
  const signals = runMechanicalLatencyBacktest("KNYC", contracts, [observation()], [
    quote("2026-08-13T20:38:00.000Z", 0.9, 0.91),
    quote("2026-08-13T20:39:00.000Z", 0.68, 0.7),
    quote("2026-08-13T20:40:00.000Z", 0.02, 0.03, { yesAskLow: 0.01 }),
  ]);
  assert.equal(signals.length, 1);
  assert.equal(signals[0].quoteCapturedAt, "2026-08-13T20:39:00.000Z");
  assert.ok(Math.abs((signals[0].entryPrice ?? 0) - 0.32) < 1e-9);
  assert.equal(signals[0].reactionAt, "2026-08-13T20:40:00.000Z");
  assert.equal(signals[0].reactionLagLowerSeconds, 50);
  assert.equal(signals[0].reactionLagUpperSeconds, 110);
  assert.equal(signals[0].candidateProxy, true);
  assert.equal(signals[0].executableProxy, false);
  assert.equal(signals[0].evidenceTier, "minute_candle_proxy");
});

test("aligns archive-only discovery to observation time and withholds profit", () => {
  const signals = runMechanicalLatencyBacktest(
    "KNYC",
    contracts,
    [observation({
      source: "IEM_MADIS_OMO",
      reportType: "OMO",
      receivedAt: "2026-09-01T10:00:00.000Z",
      receiptQuality: "discovery_only",
      settlementCompatible: false,
    })],
    [quote("2026-08-13T20:38:00.000Z")],
  );
  assert.equal(signals.length, 1);
  assert.equal(signals[0].triggeredAt, "2026-08-13T20:37:00.000Z");
  assert.equal(signals[0].alignmentClock, "observation_time_only");
  assert.equal(signals[0].candidateProxy, false);
  assert.equal(signals[0].grossProfitPerContract, null);
  assert.equal(signals[0].profitStatus, "none");
  assert.match(signals[0].limitation ?? "", /original public receipt/i);
});

test("can require settlement compatibility instead of retaining candidates", () => {
  const signals = runMechanicalLatencyBacktest(
    "KNYC",
    contracts,
    [observation({ settlementCompatible: false })],
    [quote("2026-08-13T20:39:00.000Z")],
    { ...DEFAULT_ENGINE_CONFIG, requireSettlementCompatible: true },
  );
  assert.equal(signals.length, 0);
});

test("keeps an unverified source as a discovery candidate by default", () => {
  const signals = runMechanicalLatencyBacktest(
    "KNYC",
    contracts,
    [observation({ settlementCompatible: false })],
    [quote("2026-08-13T20:39:00.000Z")],
  );
  assert.equal(signals.length, 1);
  assert.equal(signals[0].hardStateProven, false);
  assert.equal(signals[0].candidateProxy, false);
  assert.match(signals[0].limitation ?? "", /transformation is unverified/i);
});

test("does not invalidate a band that contains the confirmed degree", () => {
  const signals = runMechanicalLatencyBacktest(
    "KNYC",
    contracts,
    [observation({ temperatureF: 84.1 })],
    [quote("2026-08-13T20:39:00.000Z")],
  );
  assert.equal(signals.length, 0);
});

test("uses the explicit six-hour maximum as the report trigger", () => {
  const signals = runMechanicalLatencyBacktest(
    "KNYC",
    contracts,
    [observation({
      reportType: "METAR",
      temperatureF: 83.84,
      maxTemperatureF: 84.92,
      maxTemperatureKind: "metar_6h_max",
    })],
    [quote("2026-08-13T20:39:00.000Z")],
  );
  assert.equal(signals.length, 1);
  assert.equal(signals[0].triggerKind, "metar_6h_max");
  assert.equal(signals[0].runningHighF, 84.92);
});

test("labels a large bid collapse as violent from candle lows", () => {
  const signals = runMechanicalLatencyBacktest("KNYC", contracts, [observation()], [
    quote("2026-08-13T20:38:00.000Z", 0.68, 0.7),
    quote("2026-08-13T20:39:00.000Z", 0.6, 0.62),
    quote("2026-08-13T20:40:00.000Z", 0.02, 0.03, {
      yesBidLow: 0.01,
      yesAskLow: 0.02,
    }),
  ]);
  assert.equal(signals[0].violent, true);
  assert.ok(Math.abs((signals[0].violentMoveCents ?? 0) - 67) < 1e-9);
  assert.ok(Math.abs((signals[0].staleEdgeCents ?? 0) - 60) < 1e-9);
});

test("does not call a collapse violent when the pre-trigger quote is stale", () => {
  const signals = runMechanicalLatencyBacktest("KNYC", contracts, [observation()], [
    quote("2026-08-13T20:30:00.000Z", 0.68, 0.7),
    quote("2026-08-13T20:39:00.000Z", 0.6, 0.62),
    quote("2026-08-13T20:40:00.000Z", 0.01, 0.02, { yesBidLow: 0.01 }),
  ]);
  assert.equal(signals[0].preTriggerYesBid, null);
  assert.equal(signals[0].violent, false);
});

test("records competing NO taker trades but still does not certify our fill", () => {
  const signals = runMechanicalLatencyBacktest(
    "KNYC",
    contracts,
    [observation()],
    [quote("2026-08-13T20:38:00.000Z"), quote("2026-08-13T20:39:00.000Z", 0.6, 0.62)],
    DEFAULT_ENGINE_CONFIG,
    [
      trade(),
      trade({ tradeId: "yes-taker", takerOutcomeSide: "yes", quantity: 20 }),
      trade({ tradeId: "too-early", createdAt: "2026-08-13T20:38:10.500Z", quantity: 30 }),
    ],
  );
  assert.equal(signals[0].evidenceTier, "trade_tape_observed");
  assert.equal(signals[0].observedNoTakerQuantity, 10);
  assert.equal(signals[0].observedNoTakerVwap, 0.4);
  assert.ok((signals[0].tapeCounterfactualNetProfit ?? 0) > 0);
  assert.equal(signals[0].profitStatus, "counterfactual_tape");
  assert.equal(signals[0].executableProxy, false);
  assert.match(signals[0].limitation ?? "", /another taker/i);
});

test("does not attach trade-tape profit to an unverified transformation", () => {
  const signals = runMechanicalLatencyBacktest(
    "KNYC",
    contracts,
    [observation({ settlementCompatible: false })],
    [quote("2026-08-13T20:39:00.000Z")],
    DEFAULT_ENGINE_CONFIG,
    [trade()],
  );
  assert.equal(signals[0].observedNoTakerQuantity, 0);
  assert.equal(signals[0].tapeCounterfactualNetProfit, null);
  assert.equal(signals[0].profitStatus, "none");
});
