export const PRICE_SCALE = 10_000; // $0.0001
export const QTY_SCALE = 100; // 0.01 contracts
export const MONEY_MICRO_SCALE = 1_000_000; // $0.000001 exact product scale for price*qty

function parseFixedDecimal(value: string | number, scale: number, maxDecimals: number) {
  const text = typeof value === "number" ? value.toFixed(maxDecimals) : String(value).trim();
  if (!/^-?\d+(?:\.\d+)?$/.test(text)) throw new Error(`BAD_FIXED_DECIMAL:${text}`);
  const negative = text.startsWith("-");
  const unsigned = negative ? text.slice(1) : text;
  const [whole, fractionRaw = ""] = unsigned.split(".");
  if (fractionRaw.length > maxDecimals && /[1-9]/.test(fractionRaw.slice(maxDecimals))) {
    throw new Error(`TOO_MANY_DECIMALS:${text}`);
  }
  const fraction = (fractionRaw.slice(0, maxDecimals) + "0".repeat(maxDecimals)).slice(0, maxDecimals);
  const units = Number(whole) * scale + Number(fraction || 0) * (scale / 10 ** maxDecimals);
  if (!Number.isSafeInteger(units)) throw new Error(`UNSAFE_FIXED_DECIMAL:${text}`);
  return negative ? -units : units;
}

export function parsePriceFp(value: string | number) {
  const units = parseFixedDecimal(value, PRICE_SCALE, 4);
  if (units < 0 || units > PRICE_SCALE) throw new Error(`BAD_PRICE:${value}`);
  return units;
}

export function parseQtyFp(value: string | number) {
  const units = parseFixedDecimal(value, QTY_SCALE, 2);
  if (units < 0) throw new Error(`BAD_QTY:${value}`);
  return units;
}

export function priceFpToDollars(units: number) {
  return units / PRICE_SCALE;
}

export function qtyFpToContracts(units: number) {
  return units / QTY_SCALE;
}

export function complementPriceFp(units: number) {
  if (!Number.isSafeInteger(units) || units < 0 || units > PRICE_SCALE) throw new Error(`BAD_PRICE_FP:${units}`);
  return PRICE_SCALE - units;
}

/** Exact dollar cost represented in micro-dollars (1e-6 dollars). */
export function notionalMicros(priceFp: number, qtyFp: number) {
  const product = priceFp * qtyFp;
  if (!Number.isSafeInteger(product)) throw new Error("NOTIONAL_OVERFLOW");
  // priceFp/1e4 * qtyFp/1e2 dollars = product / 1e6 dollars.
  return product;
}

export function microsToDollars(micros: number) {
  return micros / MONEY_MICRO_SCALE;
}

export function formatPriceFp(units: number) {
  return (units / PRICE_SCALE).toFixed(4);
}

export function formatQtyFp(units: number) {
  return (units / QTY_SCALE).toFixed(2);
}
