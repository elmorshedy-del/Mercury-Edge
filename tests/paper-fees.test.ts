import assert from "node:assert/strict";
import test from "node:test";
import { predictionTakerFee } from "../lib/paper/fees";

test("replays quadratic trade fee and retail balance rounding", () => {
  const quote = predictionTakerFee({ feeType: "quadratic", feeMultiplier: 1, contracts: 10, price: 0.33 });
  assert.equal(quote.supported, true);
  // Raw formula is $0.15477; exchange trade-fee rounding lifts it to $0.1548.
  assert.equal(quote.tradeFee, 0.1548);
  // Retail cash-balance precision produces a $0.16 net fee for this isolated fill.
  assert.equal(quote.netFee, 0.16);
  assert.equal(quote.fills.length, 1);
});

test("supports fractional contracts instead of silently approximating them", () => {
  const quote = predictionTakerFee({ feeType: "quadratic", feeMultiplier: 1, contracts: 1.5, price: 0.33 });
  assert.equal(quote.supported, true);
  assert.equal(quote.fills[0].qtyFp, 150);
  assert.ok((quote.netFee ?? 0) > 0);
});

test("supports four-decimal subcent prices on the fixed-point wire scale", () => {
  const quote = predictionTakerFee({ feeType: "quadratic", feeMultiplier: 1, contracts: 1, price: 0.3333 });
  assert.equal(quote.supported, true);
  assert.equal(quote.fills[0].priceFp, 3333);
});

test("blocks fee schedules we have not modeled", () => {
  const quote = predictionTakerFee({ feeType: "flat", feeMultiplier: 1, contracts: 1, price: 0.33 });
  assert.equal(quote.supported, false);
  assert.equal(quote.reason, "FLAT_FEE_SERIES_NOT_MODELED");
});
