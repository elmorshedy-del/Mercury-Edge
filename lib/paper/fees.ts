export type FeeType = "quadratic" | "quadratic_with_maker_fees" | "flat";

export type FeeQuote = {
  supported: boolean;
  tradeFee: number | null;
  cashFeeUpperBound: number | null;
  reason: string | null;
  formula: string;
};

function ceilTo(value: number, increment: number) {
  return Math.ceil((value - 1e-12) / increment) * increment;
}

/**
 * Phase-1 fee model for prediction contracts.
 *
 * We intentionally support only whole-contract, linear-cent taker fills here.
 * The July 7, 2026 Kalshi fee schedule gives the general taker formula:
 *   M * 0.07 * C * P * (1-P)
 * and user balances are cent-aligned. For whole contracts at whole-cent prices,
 * the conservative cash fee for an isolated order is the formula rounded up to
 * the next cent. Subpenny/fractional orders require the exchange's centicent
 * fee accumulator and are blocked from Phase 1 rather than approximated.
 */
export function predictionTakerFee(input: {
  feeType: FeeType;
  feeMultiplier: number;
  contracts: number;
  price: number;
  linearCent: boolean;
}): FeeQuote {
  if (!Number.isInteger(input.contracts) || input.contracts <= 0) {
    return { supported: false, tradeFee: null, cashFeeUpperBound: null, reason: "PHASE1_WHOLE_CONTRACTS_ONLY", formula: "M*0.07*C*P*(1-P)" };
  }
  if (!input.linearCent) {
    return { supported: false, tradeFee: null, cashFeeUpperBound: null, reason: "PHASE1_LINEAR_CENT_ONLY", formula: "M*0.07*C*P*(1-P)" };
  }
  if (input.feeType === "flat") {
    return { supported: false, tradeFee: null, cashFeeUpperBound: null, reason: "FLAT_FEE_SERIES_NOT_MODELED", formula: "series-specific" };
  }
  if (!Number.isFinite(input.feeMultiplier) || input.feeMultiplier < 0) {
    return { supported: false, tradeFee: null, cashFeeUpperBound: null, reason: "BAD_FEE_MULTIPLIER", formula: "M*0.07*C*P*(1-P)" };
  }
  if (!Number.isFinite(input.price) || input.price < 0 || input.price > 1) {
    return { supported: false, tradeFee: null, cashFeeUpperBound: null, reason: "BAD_PRICE", formula: "M*0.07*C*P*(1-P)" };
  }

  const raw = input.feeMultiplier * 0.07 * input.contracts * input.price * (1 - input.price);
  // Official formula is rounded on the exchange at finer precision, while the
  // user cash balance is cent-aligned. This is deliberately a conservative
  // upper-bound for Phase-1 whole-cent orders.
  const cashFeeUpperBound = ceilTo(raw, 0.01);
  return {
    supported: true,
    tradeFee: raw,
    cashFeeUpperBound,
    reason: null,
    formula: "M*0.07*C*P*(1-P); cash fee conservatively rounded up to $0.01 for Phase 1",
  };
}
