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
    linearCent: true,
    ...overrides,
  };
}

function book(receivedEpochMs: number, yesQty = 10) {
  const b = new KalshiOrderBook("WX");
  b.applySnapshot({ seq: receivedEpochMs, receivedEpochMs, yes: [["0.6700", String(yesQty)]], no: [] });
  return b;
}

test("latency replay waits for the first book received at or after simulated arrival", () => {
  const q = new LatencyReplayQueue();
  q.add(intent(), [250]);

  assert.equal(q.onBook(book(1_200), 1_200).length, 0);
  const executions = q.onBook(book(1_260), 1_260);
  assert.equal(executions.length, 1);
  assert.equal(executions[0].arrivalEpochMs, 1_250);
  assert.equal(executions[0].bookReceivedEpochMs, 1_260);
  assert.equal(executions[0].fill.avgPrice, 0.33);
});

test("FOK refuses a partial fill rather than pretending available best price filled everything", () => {
  const b = book(1_500, 2);
  const result = executeTakerAtBook({ intent: intent({ qty: 5, timeInForce: "fok" }), latencyMs: 0, book: b, bookReceivedEpochMs: 1_500 });
  assert.equal(result.status, "unfilled");
  assert.equal(result.fill.filledQty, 0);
  assert.equal(result.totalCost, 0);
});

test("IOC partial with unsupported fractional fee path blocks rather than fabricating a fee", () => {
  const b = new KalshiOrderBook("WX");
  b.applySnapshot({ seq: 1, receivedEpochMs: 1_500, yes: [["0.6700", "1.50"]], no: [] });
  const result = executeTakerAtBook({ intent: intent({ qty: 2, timeInForce: "ioc" }), latencyMs: 0, book: b, bookReceivedEpochMs: 1_500 });
  assert.equal(result.status, "blocked");
  assert.match(result.blockReason ?? "", /FEE_MODEL_BLOCKED/);
});
