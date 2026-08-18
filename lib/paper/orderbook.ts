import {
  PRICE_SCALE,
  QTY_SCALE,
  complementPriceFp,
  microsToDollars,
  notionalMicros,
  parsePriceFp,
  parseQtyFp,
  priceFpToDollars,
  qtyFpToContracts,
} from "./fixed";

export type BookSide = "yes" | "no";
export type PriceLevel = {
  priceFp: number;
  qtyFp: number;
  price: number;
  qty: number;
};

export type KalshiBookSnapshot = {
  marketTicker: string;
  seq: number;
  exchangeTsMs: number | null;
  receivedEpochMs: number;
  yesBids: PriceLevel[];
  noBids: PriceLevel[];
};

function makeLevel(priceFp: number, qtyFp: number): PriceLevel {
  return {
    priceFp,
    qtyFp,
    price: priceFpToDollars(priceFp),
    qty: qtyFpToContracts(qtyFp),
  };
}

function normalize(levels: Map<number, number>): PriceLevel[] {
  return [...levels.entries()]
    .filter(([, qtyFp]) => qtyFp > 0)
    .map(([priceFp, qtyFp]) => makeLevel(priceFp, qtyFp))
    .sort((a, b) => a.priceFp - b.priceFp);
}

/**
 * Sequence numbers are scoped to a WebSocket subscription stream (sid), not
 * one market. Validate them outside individual market books so interleaved
 * updates across tickers cannot create false gap alarms.
 */
export class SubscriptionSequenceGuard {
  private lastBySid = new Map<number, number>();

  observe(sid: number, seq: number) {
    if (!Number.isSafeInteger(sid) || !Number.isSafeInteger(seq)) {
      throw new Error(`BAD_WS_SEQUENCE sid=${sid} seq=${seq}`);
    }
    const prior = this.lastBySid.get(sid);
    if (prior !== undefined && seq !== prior + 1) {
      throw new Error(`ORDERBOOK_SEQUENCE_GAP sid=${sid}: expected ${prior + 1}, got ${seq}`);
    }
    this.lastBySid.set(sid, seq);
  }

  reset(sid?: number) {
    if (sid === undefined) this.lastBySid.clear();
    else this.lastBySid.delete(sid);
  }

  last(sid: number) {
    return this.lastBySid.get(sid) ?? null;
  }
}

export class KalshiOrderBook {
  readonly marketTicker: string;
  private yes = new Map<number, number>(); // native YES-bid price fp -> qty fp
  private no = new Map<number, number>(); // native NO-bid price fp -> qty fp
  private lastSourceSeq = 0;
  private exchangeTsMs: number | null = null;
  private receivedEpochMs = 0;

  constructor(marketTicker: string) {
    this.marketTicker = marketTicker;
  }

  applySnapshot(input: {
    seq: number;
    exchangeTsMs?: number | null;
    receivedEpochMs: number;
    yes: Array<[string | number, string | number]>;
    no: Array<[string | number, string | number]>;
  }) {
    this.yes.clear();
    this.no.clear();
    for (const [p, q] of input.yes) this.setLevelFp("yes", parsePriceFp(p), parseQtyFp(q));
    for (const [p, q] of input.no) this.setLevelFp("no", parsePriceFp(p), parseQtyFp(q));
    this.lastSourceSeq = input.seq;
    this.exchangeTsMs = input.exchangeTsMs ?? null;
    this.receivedEpochMs = input.receivedEpochMs;
  }

  applyDelta(input: {
    seq: number;
    exchangeTsMs?: number | null;
    receivedEpochMs: number;
    side: BookSide;
    price: string | number;
    delta: string | number;
  }) {
    const priceFp = parsePriceFp(input.price);
    const deltaFp = parseSignedQtyFp(input.delta);
    const map = input.side === "yes" ? this.yes : this.no;
    const next = (map.get(priceFp) ?? 0) + deltaFp;
    if (next < 0) throw new Error(`ORDERBOOK_NEGATIVE_QTY ${this.marketTicker} ${input.side} ${input.price}`);
    this.setLevelFp(input.side, priceFp, next);
    this.lastSourceSeq = input.seq;
    this.exchangeTsMs = input.exchangeTsMs ?? this.exchangeTsMs;
    this.receivedEpochMs = input.receivedEpochMs;
  }

  private setLevelFp(side: BookSide, priceFp: number, qtyFp: number) {
    if (!Number.isSafeInteger(priceFp) || priceFp < 0 || priceFp > PRICE_SCALE) throw new Error(`BAD_PRICE_FP ${priceFp}`);
    if (!Number.isSafeInteger(qtyFp) || qtyFp < 0) throw new Error(`BAD_QTY_FP ${qtyFp}`);
    const map = side === "yes" ? this.yes : this.no;
    if (qtyFp === 0) map.delete(priceFp);
    else map.set(priceFp, qtyFp);
  }

  snapshot(): KalshiBookSnapshot {
    return {
      marketTicker: this.marketTicker,
      seq: this.lastSourceSeq,
      exchangeTsMs: this.exchangeTsMs,
      receivedEpochMs: this.receivedEpochMs,
      yesBids: normalize(this.yes),
      noBids: normalize(this.no),
    };
  }

  bestYesBid() { return normalize(this.yes).at(-1) ?? null; }
  bestNoBid() { return normalize(this.no).at(-1) ?? null; }

  // Internal representation always uses native outcome-leg bid prices.
  // Kalshi exposes bids only: NO bid y == YES ask (1-y), same quantity;
  // YES bid x == NO ask (1-x), same quantity.
  yesAsks(): PriceLevel[] {
    return normalize(this.no)
      .map((l) => makeLevel(complementPriceFp(l.priceFp), l.qtyFp))
      .sort((a, b) => a.priceFp - b.priceFp);
  }

  noAsks(): PriceLevel[] {
    return normalize(this.yes)
      .map((l) => makeLevel(complementPriceFp(l.priceFp), l.qtyFp))
      .sort((a, b) => a.priceFp - b.priceFp);
  }
}

function parseSignedQtyFp(value: string | number) {
  const text = String(value).trim();
  const negative = text.startsWith("-");
  const absolute = negative ? text.slice(1) : text;
  const units = parseQtyFp(absolute);
  return negative ? -units : units;
}

export type SimulatedFillLevel = {
  priceFp: number;
  qtyFp: number;
  price: number;
  qty: number;
  notionalMicros: number;
};

export type SimulatedFill = {
  requestedQty: number;
  requestedQtyFp: number;
  filledQty: number;
  filledQtyFp: number;
  avgPrice: number | null;
  avgPriceFpApprox: number | null;
  grossCost: number;
  grossCostMicros: number;
  worstPrice: number | null;
  worstPriceFp: number | null;
  levels: SimulatedFillLevel[];
};

export function consumeAsTaker(book: KalshiOrderBook, outcome: BookSide, qty: number, maxPrice = 1): SimulatedFill {
  const requestedQtyFp = parseQtyFp(qty);
  const maxPriceFp = parsePriceFp(maxPrice);
  const asks = outcome === "yes" ? book.yesAsks() : book.noAsks();
  let remainingFp = requestedQtyFp;
  let costMicros = 0;
  const levels: SimulatedFillLevel[] = [];

  for (const bookLevel of asks) {
    if (remainingFp <= 0 || bookLevel.priceFp > maxPriceFp) break;
    const takeFp = Math.min(bookLevel.qtyFp, remainingFp);
    if (takeFp <= 0) continue;
    const levelNotionalMicros = notionalMicros(bookLevel.priceFp, takeFp);
    levels.push({
      priceFp: bookLevel.priceFp,
      qtyFp: takeFp,
      price: priceFpToDollars(bookLevel.priceFp),
      qty: qtyFpToContracts(takeFp),
      notionalMicros: levelNotionalMicros,
    });
    costMicros += levelNotionalMicros;
    remainingFp -= takeFp;
  }

  const filledQtyFp = requestedQtyFp - remainingFp;
  const avgPrice = filledQtyFp > 0
    ? microsToDollars(costMicros) / qtyFpToContracts(filledQtyFp)
    : null;

  return {
    requestedQty: qtyFpToContracts(requestedQtyFp),
    requestedQtyFp,
    filledQty: qtyFpToContracts(filledQtyFp),
    filledQtyFp,
    avgPrice,
    avgPriceFpApprox: avgPrice === null ? null : Math.round(avgPrice * PRICE_SCALE),
    grossCost: microsToDollars(costMicros),
    grossCostMicros: costMicros,
    worstPrice: levels.length ? levels[levels.length - 1].price : null,
    worstPriceFp: levels.length ? levels[levels.length - 1].priceFp : null,
    levels,
  };
}
