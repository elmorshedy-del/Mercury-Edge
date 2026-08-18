import assert from "node:assert/strict";
import test from "node:test";
import { predictionTakerFeesForFills } from "../lib/paper/fees";
import { complementPriceFp, parsePriceFp, parseQtyFp } from "../lib/paper/fixed";
import { normalizeNativePrice } from "../lib/paper/kalshi-ws";
import { KalshiOrderBook, SubscriptionSequenceGuard, consumeAsTaker } from "../lib/paper/orderbook";
import { LatencyReplayEngine, executeTakerAtBook, type PaperOrderIntent } from "../lib/paper/simulator";

function intent(overrides: Partial<PaperOrderIntent> = {}): PaperOrderIntent {
  return {
    signalId: "sig-1",
    strategyCode: "DBN",
    marketTicker: "TEST",
    outcome: "no",
    qty: 1,
    maxPrice: 1,
    decisionEpochMs: 1_100,
    timeInForce: "ioc",
    feeType: "quadratic",
    feeMultiplier: 1,
    ...overrides,
  };
}

function bookAt(receivedEpochMs: number, yesBid: string, yesQty = "10.00") {
  const book = new KalshiOrderBook("TEST");
  book.applySnapshot({
    seq: 1,
    receivedEpochMs,
    yes: [[yesBid, yesQty]],
    no: [["0.1000", "10.00"]],
  });
  return book;
}

test("fixed-point parsing and binary complement are exact", () => {
  assert.equal(parsePriceFp("0.0550"), 550);
  assert.equal(parseQtyFp("1.55"), 155);
  assert.equal(complementPriceFp(550), 9_450);
  assert.throws(() => parsePriceFp("0.05501"), /TOO_MANY_DECIMALS/);
  assert.throws(() => parseQtyFp("1.551"), /TOO_MANY_DECIMALS/);
});

test("NO ask is exact complement of YES bid with identical size", () => {
  const book = bookAt(1_000, "0.2700", "5.00");
  const asks = book.noAsks();
  assert.equal(asks.length, 1);
  assert.equal(asks[0].priceFp, 7_300);
  assert.equal(asks[0].qtyFp, 500);

  const fill = consumeAsTaker(book, "no", 3, 0.73);
  assert.equal(fill.filledQtyFp, 300);
  assert.equal(fill.grossCostMicros, 2_190_000);
  assert.equal(fill.avgPrice, 0.73);
});

test("unified YES-scale NO levels normalize back to native NO pricing", () => {
  assert.equal(normalizeNativePrice("yes", "0.7000", true), "0.7000");
  assert.equal(normalizeNativePrice("no", "0.7000", true), "0.3000");
  assert.equal(normalizeNativePrice("no", "0.3000", false), "0.3000");
});

test("sequence validation is subscription-scoped, not market-scoped", () => {
  const guard = new SubscriptionSequenceGuard();
  guard.observe(4, 100);
  // These could belong to different market tickers on the same sid.
  guard.observe(4, 101);
  guard.observe(4, 102);
  assert.equal(guard.last(4), 102);
  assert.throws(() => guard.observe(4, 104), /ORDERBOOK_SEQUENCE_GAP/);
  guard.observe(9, 1);
  assert.equal(guard.last(9), 1);
});

test("fee accumulator reproduces published subpenny rounding mechanics", () => {
  // At multiplier 2.33, the quadratic raw fee for 1 contract @ $0.055 is
  // $0.008477..., which the exchange rounds up to $0.0085 per fill. The
  // subsequent balance rounding/rebate sequence is the published 0.0065,
  // rebate-on-fill-2 example.
  const quote = predictionTakerFeesForFills({
    feeType: "quadratic",
    feeMultiplier: 2.33,
    fills: [
      { priceFp: 550, qtyFp: 100 },
      { priceFp: 550, qtyFp: 100 },
      { priceFp: 550, qtyFp: 100 },
    ],
  });
  assert.equal(quote.supported, true);
  assert.deepEqual(quote.fills.map((x) => x.tradeFeeMicros), [8_500, 8_500, 8_500]);
  assert.deepEqual(quote.fills.map((x) => x.roundingFeeMicros), [6_500, 6_500, 6_500]);
  assert.deepEqual(quote.fills.map((x) => x.rebateMicros), [0, 10_000, 0]);
  assert.deepEqual(quote.fills.map((x) => x.netFeeMicros), [15_000, 5_000, 15_000]);
  assert.equal(quote.netFeeMicros, 35_000);
});

test("replay uses the last state valid at arrival, not the next update", () => {
  const liveBook = bookAt(1_000, "0.6000"); // NO ask = 0.40
  const replay = new LatencyReplayEngine();
  replay.onBookUpdate(liveBook, 1_000);
  replay.add(intent(), [100]); // arrival = 1,200

  // Mutate the live object to a future state before handing the 1,500 event to
  // replay. The stored 1,000 checkpoint must remain immutable.
  liveBook.applyDelta({
    seq: 2,
    receivedEpochMs: 1_500,
    side: "yes",
    price: "0.6000",
    delta: "-10.00",
  });
  const executions = replay.onBookUpdate(liveBook, 1_500);
  assert.equal(executions.length, 1);
  assert.equal(executions[0].arrivalEpochMs, 1_200);
  assert.equal(executions[0].bookReceivedEpochMs, 1_000);
  assert.equal(executions[0].fill.avgPrice, 0.4);
  assert.equal(executions[0].status, "filled");
});

test("same-millisecond market update wins tie conservatively", () => {
  const liveBook = bookAt(1_000, "0.6000");
  const replay = new LatencyReplayEngine();
  replay.onBookUpdate(liveBook, 1_000);
  replay.add(intent(), [100]); // arrival exactly 1,200

  liveBook.applyDelta({
    seq: 2,
    receivedEpochMs: 1_200,
    side: "yes",
    price: "0.6000",
    delta: "-10.00",
  });
  const executions = replay.onBookUpdate(liveBook, 1_200);
  assert.equal(executions.length, 1);
  assert.equal(executions[0].status, "unfilled");
  assert.equal(executions[0].bookReceivedEpochMs, 1_200);
});

test("FOK never converts a partial book walk into a partial paper fill", () => {
  const book = bookAt(1_000, "0.6000", "0.50");
  const execution = executeTakerAtBook({
    intent: intent({ qty: 1, timeInForce: "fok", decisionEpochMs: 1_000 }),
    latencyMs: 0,
    book,
    stateKnownThroughEpochMs: 1_000,
  });
  assert.equal(execution.status, "unfilled");
  assert.equal(execution.fill.filledQtyFp, 0);
  assert.equal(execution.totalCost, 0);
});

test("future book state is blocked rather than used", () => {
  const book = bookAt(1_010, "0.6000");
  const execution = executeTakerAtBook({
    intent: intent({ decisionEpochMs: 1_000 }),
    latencyMs: 0,
    book,
    stateKnownThroughEpochMs: 1_010,
  });
  assert.equal(execution.status, "blocked");
  assert.equal(execution.blockReason, "FUTURE_BOOK_LEAKAGE");
});
