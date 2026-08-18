import assert from "node:assert/strict";
import test from "node:test";
import { KalshiOrderBook, SubscriptionSequenceGuard, consumeAsTaker } from "../lib/paper/orderbook";

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
  const noAsk = b.noAsks()[0];
  const yesAsk = b.yesAsks()[0];
  assert.equal(noAsk.price, 0.33);
  assert.equal(noAsk.qty, 3);
  assert.equal(noAsk.priceFp, 3300);
  assert.equal(noAsk.qtyFp, 300);
  assert.equal(yesAsk.price, 0.69);
  assert.equal(yesAsk.qty, 4);
});

test("walks real depth rather than assuming the best quote fills all quantity", () => {
  const b = book();
  const fill = consumeAsTaker(b, "no", 8, 0.35);
  assert.equal(fill.filledQty, 8);
  assert.equal(fill.levels.length, 2);
  assert.equal(fill.levels[0].price, 0.33);
  assert.equal(fill.levels[0].qty, 3);
  assert.equal(fill.levels[1].price, 0.35);
  assert.equal(fill.levels[1].qty, 5);
  assert.equal(fill.avgPrice, 0.3425);
  assert.equal(fill.grossCost, 2.74);
});

test("validates sequence continuity at the subscription stream, not per market", () => {
  const guard = new SubscriptionSequenceGuard();
  guard.observe(7, 10);
  guard.observe(7, 11); // could belong to another market on the same sid
  assert.throws(() => guard.observe(7, 13), /ORDERBOOK_SEQUENCE_GAP/);
  guard.reset(7);
  guard.observe(7, 40); // fresh subscription generation can begin at any seq
});

test("complements four-decimal subcent prices exactly", () => {
  const b = new KalshiOrderBook("SUBPENNY");
  b.applySnapshot({ seq: 1, receivedEpochMs: 1, yes: [["0.3333", "1.00"]], no: [] });
  assert.equal(b.noAsks()[0].price, 0.6667);
  assert.equal(b.noAsks()[0].priceFp, 6667);
});
