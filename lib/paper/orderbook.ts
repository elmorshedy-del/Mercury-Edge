export type BookSide = "yes" | "no";
export type PriceLevel = { price: number; qty: number };

export type KalshiBookSnapshot = {
  marketTicker: string;
  seq: number;
  exchangeTsMs: number | null;
  receivedEpochMs: number;
  yesBids: PriceLevel[];
  noBids: PriceLevel[];
};

const PRICE_SCALE = 1_000_000;

/** Kalshi dollar prices are fixed-point to at most 6 decimals on current APIs. */
export function quantizePrice(value: number) {
  return Math.round(value * PRICE_SCALE) / PRICE_SCALE;
}

function normalize(levels: Map<number, number>): PriceLevel[] {
  return [...levels.entries()]
    .filter(([, qty]) => qty > 0)
    .map(([price, qty]) => ({ price, qty }))
    .sort((a, b) => a.price - b.price);
}

export class KalshiOrderBook {
  readonly marketTicker: string;
  private yes = new Map<number, number>();
  private no = new Map<number, number>();
  private lastSeq = 0;
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
    for (const [p, q] of input.yes) this.setLevel("yes", Number(p), Number(q));
    for (const [p, q] of input.no) this.setLevel("no", Number(p), Number(q));
    this.lastSeq = input.seq;
    this.exchangeTsMs = input.exchangeTsMs ?? null;
    this.receivedEpochMs = input.receivedEpochMs;
  }

  applyDelta(input: {
    seq: number;
    exchangeTsMs?: number | null;
    receivedEpochMs: number;
    side: BookSide;
    price: number;
    delta: number;
  }) {
    if (this.lastSeq && input.seq !== this.lastSeq + 1) {
      throw new Error(`ORDERBOOK_SEQUENCE_GAP ${this.marketTicker}: expected ${this.lastSeq + 1}, got ${input.seq}`);
    }
    const price = quantizePrice(input.price);
    const map = input.side === "yes" ? this.yes : this.no;
    const next = (map.get(price) ?? 0) + input.delta;
    if (next < -1e-9) throw new Error(`ORDERBOOK_NEGATIVE_QTY ${this.marketTicker} ${input.side} ${price}`);
    this.setLevel(input.side, price, Math.max(0, next));
    this.lastSeq = input.seq;
    this.exchangeTsMs = input.exchangeTsMs ?? this.exchangeTsMs;
    this.receivedEpochMs = input.receivedEpochMs;
  }

  private setLevel(side: BookSide, rawPrice: number, qty: number) {
    const price = quantizePrice(rawPrice);
    if (!Number.isFinite(price) || price < 0 || price > 1) throw new Error(`BAD_PRICE ${rawPrice}`);
    if (!Number.isFinite(qty) || qty < 0) throw new Error(`BAD_QTY ${qty}`);
    const map = side === "yes" ? this.yes : this.no;
    if (qty === 0) map.delete(price);
    else map.set(price, qty);
  }

  snapshot(): KalshiBookSnapshot {
    return {
      marketTicker: this.marketTicker,
      seq: this.lastSeq,
      exchangeTsMs: this.exchangeTsMs,
      receivedEpochMs: this.receivedEpochMs,
      yesBids: normalize(this.yes),
      noBids: normalize(this.no),
    };
  }

  bestYesBid() { return normalize(this.yes).at(-1) ?? null; }
  bestNoBid() { return normalize(this.no).at(-1) ?? null; }

  // Kalshi exposes bids only. A NO bid at y is a YES ask at 1-y,
  // and a YES bid at x is a NO ask at 1-x. Quantity is identical.
  yesAsks(): PriceLevel[] {
    return normalize(this.no)
      .map((l) => ({ price: quantizePrice(1 - l.price), qty: l.qty }))
      .sort((a, b) => a.price - b.price);
  }

  noAsks(): PriceLevel[] {
    return normalize(this.yes)
      .map((l) => ({ price: quantizePrice(1 - l.price), qty: l.qty }))
      .sort((a, b) => a.price - b.price);
  }
}

export type SimulatedFill = {
  requestedQty: number;
  filledQty: number;
  avgPrice: number | null;
  grossCost: number;
  worstPrice: number | null;
  levels: Array<{ price: number; qty: number }>;
};

export function consumeAsTaker(book: KalshiOrderBook, outcome: BookSide, qty: number, maxPrice = 1): SimulatedFill {
  const asks = outcome === "yes" ? book.yesAsks() : book.noAsks();
  let remaining = qty;
  let cost = 0;
  const levels: Array<{ price: number; qty: number }> = [];
  const limit = quantizePrice(maxPrice);
  for (const level of asks) {
    if (remaining <= 1e-9 || level.price > limit) break;
    const take = Math.min(level.qty, remaining);
    if (take <= 0) continue;
    levels.push({ price: level.price, qty: take });
    cost += take * level.price;
    remaining -= take;
  }
  const filledQty = qty - remaining;
  return {
    requestedQty: qty,
    filledQty,
    avgPrice: filledQty > 0 ? quantizePrice(cost / filledQty) : null,
    grossCost: quantizePrice(cost),
    worstPrice: levels.length ? levels[levels.length - 1].price : null,
    levels,
  };
}
