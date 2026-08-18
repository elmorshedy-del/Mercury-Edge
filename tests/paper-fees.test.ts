import assert from "node:assert/strict";
import test from "node:test";
import { predictionTakerFee } from "../lib/paper/fees";

test("uses current quadratic taker formula and conservative cent cash bound", () => {
  const quote = predictionTakerFee({ feeType: "quadratic", feeMultiplier: 1, contracts: 10, price: 0.33, linearCent: true });
  assert.equal(quote.supported, true);
  assert.ok(Math.abs((quote.tradeFee ?? 0) - 0.15477) < 1e-12);
  assert.equal(quote.cashFeeUpperBound, 0.16);
});

test("blocks fractional contract fee approximation in phase 1", () => {
  const quote = predictionTakerFee({ feeType: "quadratic", feeMultiplier: 1, contracts: 1.5, price: 0.33, linearCent: true });
  assert.equal(quote.supported, false);
  assert.equal(quote.reason, "PHASE1_WHOLE_CONTRACTS_ONLY");
});

test("blocks subpenny markets in phase 1", () => {
  const quote = predictionTakerFee({ feeType: "quadratic", feeMultiplier: 1, contracts: 1, price: 0.333333, linearCent: false });
  assert.equal(quote.supported, false);
  assert.equal(quote.reason, "PHASE1_LINEAR_CENT_ONLY");
});
