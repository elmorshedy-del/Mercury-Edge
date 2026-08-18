import { MONEY_MICRO_SCALE, PRICE_SCALE, QTY_SCALE, microsToDollars } from "./fixed";

export type FeeType = "quadratic" | "quadratic_with_maker_fees" | "flat";

export type FeeFill = {
  priceFp: number;
  qtyFp: number;
};

export type FeeFillBreakdown = {
  priceFp: number;
  qtyFp: number;
  tradeFeeMicros: number;
  roundingFeeMicros: number;
  rebateMicros: number;
  netFeeMicros: number;
  accumulatorMicros: number;
};

export type FeeQuote = {
  supported: boolean;
  tradeFee: number | null;
  netFee: number | null;
  tradeFeeMicros: number | null;
  netFeeMicros: number | null;
  reason: string | null;
  formula: string;
  fills: FeeFillBreakdown[];
};

const TRADE_FEE_ROUNDING_MICROS = 100; // $0.0001
const RETAIL_BALANCE_PRECISION_MICROS = 10_000; // $0.01
const DIRECT_MEMBER_BALANCE_PRECISION_MICROS = 100; // $0.0001
const MULTIPLIER_SCALE = 1_000_000;

function ceilDivBigInt(numerator: bigint, denominator: bigint) {
  if (numerator < 0n || denominator <= 0n) throw new Error("BAD_CEIL_DIV");
  return (numerator + denominator - 1n) / denominator;
}

function ceilToIncrementBigInt(numerator: bigint, denominator: bigint, incrementMicros: number) {
  const increment = BigInt(incrementMicros);
  const buckets = ceilDivBigInt(numerator, denominator * increment);
  return Number(buckets * increment);
}

function multiplierFp(value: number) {
  if (!Number.isFinite(value) || value < 0) throw new Error("BAD_FEE_MULTIPLIER");
  const fp = Math.round(value * MULTIPLIER_SCALE);
  if (!Number.isSafeInteger(fp)) throw new Error("BAD_FEE_MULTIPLIER");
  return fp;
}

/**
 * Kalshi quadratic taker fee in exact fixed-point arithmetic.
 *
 * Fee schedule formula:
 *   M * 0.07 * C * P * (1-P)
 *
 * priceFp is P * 1e4 and qtyFp is C * 1e2. We compute the raw fee as a
 * rational, then apply the exchange rule that each fill's trade fee is rounded
 * UP to the nearest $0.0001 before balance rounding/rebate mechanics.
 */
export function quadraticTradeFeeMicros(input: {
  feeMultiplier: number;
  priceFp: number;
  qtyFp: number;
}) {
  if (!Number.isSafeInteger(input.priceFp) || input.priceFp < 0 || input.priceFp > PRICE_SCALE) {
    throw new Error("BAD_PRICE_FP");
  }
  if (!Number.isSafeInteger(input.qtyFp) || input.qtyFp <= 0) throw new Error("BAD_QTY_FP");
  const m = BigInt(multiplierFp(input.feeMultiplier));
  const q = BigInt(input.qtyFp);
  const p = BigInt(input.priceFp);
  const one = BigInt(PRICE_SCALE);

  // raw fee in micro-dollars:
  // (Mfp/1e6)*(7/100)*(q/1e2)*(p/1e4)*((1e4-p)/1e4)*1e6
  // = Mfp*7*q*p*(1e4-p) / 1e12
  const numerator = m * 7n * q * p * (one - p);
  const denominator = 1_000_000_000_000n;
  return ceilToIncrementBigInt(numerator, denominator, TRADE_FEE_ROUNDING_MICROS);
}

function floorToPrecision(valueMicros: number, precisionMicros: number) {
  return Math.floor(valueMicros / precisionMicros) * precisionMicros;
}

/**
 * Replays Kalshi's published fee-rounding mechanics fill by fill, including the
 * per-order rounding accumulator and whole-cent rebates for non-direct retail
 * members. For a buy, revenue is negative notional.
 */
export function predictionTakerFeesForFills(input: {
  feeType: FeeType;
  feeMultiplier: number;
  fills: FeeFill[];
  directMember?: boolean;
}): FeeQuote {
  if (input.feeType === "flat") {
    return unsupported("FLAT_FEE_SERIES_NOT_MODELED");
  }
  if (!Number.isFinite(input.feeMultiplier) || input.feeMultiplier < 0) {
    return unsupported("BAD_FEE_MULTIPLIER");
  }
  if (!input.fills.length) {
    return {
      supported: true,
      tradeFee: 0,
      netFee: 0,
      tradeFeeMicros: 0,
      netFeeMicros: 0,
      reason: null,
      formula: "M*0.07*C*P*(1-P) + Kalshi fill-level rounding accumulator",
      fills: [],
    };
  }

  const precision = input.directMember
    ? DIRECT_MEMBER_BALANCE_PRECISION_MICROS
    : RETAIL_BALANCE_PRECISION_MICROS;
  let accumulatorMicros = 0;
  let totalTradeFeeMicros = 0;
  let totalNetFeeMicros = 0;
  const breakdown: FeeFillBreakdown[] = [];

  for (const fill of input.fills) {
    if (!Number.isSafeInteger(fill.priceFp) || fill.priceFp < 0 || fill.priceFp > PRICE_SCALE) {
      return unsupported("BAD_PRICE_FP");
    }
    if (!Number.isSafeInteger(fill.qtyFp) || fill.qtyFp <= 0) return unsupported("BAD_QTY_FP");

    const tradeFeeMicros = quadraticTradeFeeMicros({
      feeMultiplier: input.feeMultiplier,
      priceFp: fill.priceFp,
      qtyFp: fill.qtyFp,
    });
    const notionalMicros = fill.priceFp * fill.qtyFp; // exact: 1e4*1e2 -> $ micro scale
    if (!Number.isSafeInteger(notionalMicros)) return unsupported("NOTIONAL_OVERFLOW");
    const revenueMicros = -notionalMicros;
    const balanceChangeMicros = revenueMicros - tradeFeeMicros;
    const floored = floorToPrecision(balanceChangeMicros, precision);
    const roundingFeeMicros = balanceChangeMicros - floored;

    accumulatorMicros += roundingFeeMicros;
    const rebateMicros = Math.floor(accumulatorMicros / RETAIL_BALANCE_PRECISION_MICROS) * RETAIL_BALANCE_PRECISION_MICROS;
    accumulatorMicros -= rebateMicros;

    const netFeeMicros = Math.max(0, tradeFeeMicros + roundingFeeMicros - rebateMicros);
    totalTradeFeeMicros += tradeFeeMicros;
    totalNetFeeMicros += netFeeMicros;
    breakdown.push({
      priceFp: fill.priceFp,
      qtyFp: fill.qtyFp,
      tradeFeeMicros,
      roundingFeeMicros,
      rebateMicros,
      netFeeMicros,
      accumulatorMicros,
    });
  }

  return {
    supported: true,
    tradeFee: microsToDollars(totalTradeFeeMicros),
    netFee: microsToDollars(totalNetFeeMicros),
    tradeFeeMicros: totalTradeFeeMicros,
    netFeeMicros: totalNetFeeMicros,
    reason: null,
    formula: "M*0.07*C*P*(1-P); trade fee ceil $0.0001/fill; balance floor + per-order rebate accumulator",
    fills: breakdown,
  };
}

/** Backward-compatible one-price quote used by older reports. */
export function predictionTakerFee(input: {
  feeType: FeeType;
  feeMultiplier: number;
  contracts: number;
  price: number;
  linearCent?: boolean;
}): FeeQuote {
  const qtyFp = Math.round(input.contracts * QTY_SCALE);
  const priceFp = Math.round(input.price * PRICE_SCALE);
  return predictionTakerFeesForFills({
    feeType: input.feeType,
    feeMultiplier: input.feeMultiplier,
    fills: qtyFp > 0 ? [{ priceFp, qtyFp }] : [],
  });
}

function unsupported(reason: string): FeeQuote {
  return {
    supported: false,
    tradeFee: null,
    netFee: null,
    tradeFeeMicros: null,
    netFeeMicros: null,
    reason,
    formula: "M*0.07*C*P*(1-P) + Kalshi fill-level rounding accumulator",
    fills: [],
  };
}

export const FEE_PRECISION = {
  moneyMicroScale: MONEY_MICRO_SCALE,
  tradeFeeRoundingMicros: TRADE_FEE_ROUNDING_MICROS,
  retailBalancePrecisionMicros: RETAIL_BALANCE_PRECISION_MICROS,
  directMemberBalancePrecisionMicros: DIRECT_MEMBER_BALANCE_PRECISION_MICROS,
};
