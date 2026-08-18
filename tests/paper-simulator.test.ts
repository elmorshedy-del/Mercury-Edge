import assert from "node:assert/strict";
import test from "node:test";
import { KalshiOrderBook } from "../lib/paper/orderbook";
import { LatencyReplayQueue, executeTakerAtBook, type PaperOrderIntent } from "../lib/paper/simulator";

function intent(overrides: Partial<PaperOrderIntent> = {}): PaperOrderIntent {
  return {
    signalId: "s1",
    strategyCode: "DBN",
    marketTicker: "WX",
    outcome: "no",
    qty: 5,
    maxPrice: 0.4,
    decisionEpochMs: 1_000,
    timeInForce: "fok",
    feeType: "quadratic",
    feeMultiplier: 1,
    ...overrides,
  };
}

function book(receivedEpochMs: number, yesQty = 10) {
  const b = new KalshiOrderBook("WX");
  b.applySnapshot({ seq: receivedEpochMs, receivedEpochMs, yes: [["0.6700", String(yesQty)]], no: [] });
  return b;
}

test("latency replay uses the last book state valid through simulated arrival", () => {
  const q = new LatencyReplayQueue();
  q.add(intent(), [250]);

  assert.equal(q.onBookUpdate(book(1_200), 1_200).length, 0);
  const executions = q.onBookUpdate(book(1_260), 1_260);
  assert.equal(executions.length, 1);
  assert.equal(executions[0].arrivalEpochMs, 1_250);
  // The 1,200 state remained valid until the 1,260 update; replay must not leak
  // the future 1,260 state backward to a 1,250 order arrival.
  assert.equal(executions[0].bookReceivedEpochMs, 1_200);
  assert.equal(executions[0].stateKnownThroughEpochMs, 1_260);
  assert.ok(Math.abs((executions[0].fill.avgPrice ?? 0) - 0.33) < 1e-12);
});

test("FOK refuses a partial fill rather than pretending available depth filled everything", () => {
  const b = book(1_500, 2);
  const result = executeTakerAtBook({
    intent: intent({ qty: 5, timeInForce: "fok", decisionEpochMs: 1_500 }),
    latencyMs: 0,
    book: b,
    stateKnownThroughEpochMs: 1_500,
  });
  assert.equal(result.status, "unfilled");
  assert.equal(result.fill.filledQty, 0);
  assert.equal(result.totalCost, 0);
});

test("IOC can model a legitimate fractional partial fill and its fees", () => {
  const b = new KalshiOrderBook("WX");
  b.applySnapshot({ seq: 1, receivedEpochMs: 1_500, yes: [["0.6700", "1.50"]], no: [] });
  const result = executeTakerAtBook({
    intent: intent({ qty: 2, timeInForce: "ioc", decisionEpochMs: 1_500 }),
    latencyMs: 0,
    book: b,
    stateKnownThroughEpochMs: 1_500,
  });
  assert.equal(result.status, "partial");
  assert.equal(result.fill.filledQty, 1.5);
  assert.ok(Math.abs((result.fill.avgPrice ?? 0) - 0.33) < 1e-12);
  assert.ok((result.fee ?? 0) > 0);
  assert.equal(result.blockReason, null);
});

test("blocks future book leakage", () => {
  const b = book(1_500, 10);
  const result = executeTakerAtBook({
    intent: intent({ decisionEpochMs: 1_000 }),
    latencyMs: 0,
    book: b,
    stateKnownThroughEpochMs: 1_500,
  });
  assert.equal(result.status, "blocked");
  assert.equal(result.blockReason, "FUTURE_BOOK_LEAKAGE");
});
