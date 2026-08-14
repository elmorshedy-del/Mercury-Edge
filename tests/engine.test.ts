import assert from "node:assert/strict";
import test from "node:test";
import { runMechanicalLatencyBacktest } from "../lib/backtest/engine";
import type { ContractBand, ObservationPoint, QuotePoint } from "../lib/types";

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

function quote(capturedAt: string, yesBid = 0.68, yesAsk = 0.7): QuotePoint {
  return { contractTicker: "LOW", capturedAt, yesBid, yesAsk };
}

test("uses the first quote after receipt and never a quote from before receipt", () => {
  const signals = runMechanicalLatencyBacktest("KNYC", contracts, [observation()], [
    quote("2026-08-13T20:38:00.000Z", 0.9, 0.91),
    quote("2026-08-13T20:39:00.000Z", 0.68, 0.7),
    quote("2026-08-13T20:40:00.000Z", 0.02, 0.03),
  ]);
  assert.equal(signals.length, 1);
  assert.equal(signals[0].quoteCapturedAt, "2026-08-13T20:39:00.000Z");
  assert.ok(Math.abs((signals[0].entryPrice ?? 0) - 0.32) < 1e-9);
  assert.equal(signals[0].reactionAt, "2026-08-13T20:40:00.000Z");
  assert.equal(signals[0].reactionLagSeconds, 110);
});

test("fails the executable gate when only discovery time survives", () => {
  const signals = runMechanicalLatencyBacktest(
    "KNYC",
    contracts,
    [observation({ source: "IEM_MADIS_OMO", reportType: "OMO", receiptQuality: "discovery_only" })],
    [quote("2026-08-13T20:39:00.000Z")],
  );
  assert.equal(signals[0].executableProxy, false);
  assert.match(signals[0].limitation ?? "", /receipt timestamp/i);
});

test("does not use a settlement-incompatible measurement", () => {
  const signals = runMechanicalLatencyBacktest(
    "KNYC",
    contracts,
    [observation({ settlementCompatible: false })],
    [quote("2026-08-13T20:39:00.000Z")],
  );
  assert.equal(signals.length, 0);
});

test("does not invalidate a band that still contains the confirmed degree", () => {
  const signals = runMechanicalLatencyBacktest(
    "KNYC",
    contracts,
    [observation({ temperatureF: 84.1 })],
    [quote("2026-08-13T20:39:00.000Z")],
  );
  assert.equal(signals.length, 0);
});
