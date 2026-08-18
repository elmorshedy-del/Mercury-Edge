import assert from "node:assert/strict";
import test from "node:test";
import { KalshiOrderBook, consumeAsTaker } from "../lib/paper/orderbook";

function book() {
  const b = new KalshiOrderBook("WX");
  b.applySnapshot({
    seq: 10,
    receivedEpochMs: 1_000,
    yes: [["0.6700", "3.00"], ["0.6500", "10.00"]],
    no: [["0.3100", "4.00"], ["0.3000", "10.00"]],
  });
  return b;
}

test("derives asks from the exact complementary bid with identical size", () => {
  const b = book();
  assert.deepEqual(b.noAsks()[0], { price: 0.33, qty: 3 });
  assert.deepEqual(b.yesAsks()[0], { price: 0.69, qty: 4 });
});

test("walks real depth rather than assuming the best quote fills all quantity", () => {
  const b = book();
  const fill = consumeAsTaker(b, "no", 8, 0.35);
  assert.equal(fill.filledQty, 8);
  assert.equal(fill.levels.length, 2);
  assert.deepEqual(fill.levels[0], { price: 0.33, qty: 3 });
  assert.deepEqual(fill.levels[1], { price: 0.35, qty: 5 });
  assert.equal(fill.avgPrice, 0.3425);
  assert.equal(fill.grossCost, 2.74);
});

test("rejects a sequence gap instead of silently mutating a corrupt book", () => {
  const b = book();
  assert.throws(() => b.applyDelta({ seq: 12, receivedEpochMs: 1_001, side: "yes", price: 0.67, delta: -1 }), /ORDERBOOK_SEQUENCE_GAP/);
});

test("quantizes complement prices to fixed-point wire precision", () => {
  const b = new KalshiOrderBook("SUBPENNY");
  b.applySnapshot({ seq: 1, receivedEpochMs: 1, yes: [["0.333333", "1"]], no: [] });
  assert.equal(b.noAsks()[0].price, 0.666667);
});
