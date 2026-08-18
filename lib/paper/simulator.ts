import { predictionTakerFeesForFills, type FeeFillBreakdown, type FeeType } from "./fees";
import { consumeAsTaker, KalshiOrderBook, type BookSide, type SimulatedFill } from "./orderbook";

export const DEFAULT_LATENCY_LADDER_MS = [0, 10, 25, 50, 100, 250, 500, 1_000, 2_000, 5_000] as const;

export type PaperOrderIntent = {
  signalId: string;
  strategyCode: string;
  marketTicker: string;
  outcome: BookSide;
  qty: number;
  maxPrice: number;
  decisionEpochMs: number;
  timeInForce: "ioc" | "fok";
  feeType: FeeType;
  feeMultiplier: number;
  directMember?: boolean;
};

export type PaperExecution = {
  signalId: string;
  strategyCode: string;
  marketTicker: string;
  outcome: BookSide;
  latencyMs: number;
  decisionEpochMs: number;
  arrivalEpochMs: number;
  bookReceivedEpochMs: number;
  stateKnownThroughEpochMs: number;
  bookSeq: number;
  status: "filled" | "partial" | "unfilled" | "blocked";
  fill: SimulatedFill;
  fee: number | null;
  feeBreakdown: FeeFillBreakdown[];
  totalCost: number | null;
  blockReason: string | null;
};

export function executeTakerAtBook(input: {
  intent: PaperOrderIntent;
  latencyMs: number;
  book: KalshiOrderBook;
  stateKnownThroughEpochMs: number;
}): PaperExecution {
  const { intent, latencyMs, book, stateKnownThroughEpochMs } = input;
  const arrivalEpochMs = intent.decisionEpochMs + latencyMs;
  const snap = book.snapshot();

  if (snap.receivedEpochMs > arrivalEpochMs) {
    return blocked(intent, latencyMs, snap.receivedEpochMs, stateKnownThroughEpochMs, snap.seq, "FUTURE_BOOK_LEAKAGE");
  }
  if (stateKnownThroughEpochMs < arrivalEpochMs) {
    return blocked(intent, latencyMs, snap.receivedEpochMs, stateKnownThroughEpochMs, snap.seq, "BOOK_STATE_NOT_KNOWN_THROUGH_ARRIVAL");
  }

  const fill = consumeAsTaker(book, intent.outcome, intent.qty, intent.maxPrice);
  if (intent.timeInForce === "fok" && fill.filledQtyFp < fill.requestedQtyFp) {
    const zero = zeroedFill(fill);
    return result(intent, latencyMs, snap.receivedEpochMs, stateKnownThroughEpochMs, snap.seq, "unfilled", zero, 0, [], 0, null);
  }

  if (fill.filledQtyFp <= 0 || fill.avgPrice === null) {
    return result(intent, latencyMs, snap.receivedEpochMs, stateKnownThroughEpochMs, snap.seq, "unfilled", fill, 0, [], 0, null);
  }

  const feeQuote = predictionTakerFeesForFills({
    feeType: intent.feeType,
    feeMultiplier: intent.feeMultiplier,
    directMember: intent.directMember,
    fills: fill.levels.map((level) => ({ priceFp: level.priceFp, qtyFp: level.qtyFp })),
  });
  if (!feeQuote.supported || feeQuote.netFee === null) {
    return blocked(
      intent,
      latencyMs,
      snap.receivedEpochMs,
      stateKnownThroughEpochMs,
      snap.seq,
      `FEE_MODEL_BLOCKED:${feeQuote.reason ?? "UNKNOWN"}`,
      fill,
    );
  }

  return result(
    intent,
    latencyMs,
    snap.receivedEpochMs,
    stateKnownThroughEpochMs,
    snap.seq,
    fill.filledQtyFp >= fill.requestedQtyFp ? "filled" : "partial",
    fill,
    feeQuote.netFee,
    feeQuote.fills,
    fill.grossCost + feeQuote.netFee,
    null,
  );
}

function result(
  intent: PaperOrderIntent,
  latencyMs: number,
  bookReceivedEpochMs: number,
  stateKnownThroughEpochMs: number,
  bookSeq: number,
  status: PaperExecution["status"],
  fill: SimulatedFill,
  fee: number | null,
  feeBreakdown: FeeFillBreakdown[],
  totalCost: number | null,
  blockReason: string | null,
): PaperExecution {
  return {
    signalId: intent.signalId,
    strategyCode: intent.strategyCode,
    marketTicker: intent.marketTicker,
    outcome: intent.outcome,
    latencyMs,
    decisionEpochMs: intent.decisionEpochMs,
    arrivalEpochMs: intent.decisionEpochMs + latencyMs,
    bookReceivedEpochMs,
    stateKnownThroughEpochMs,
    bookSeq,
    status,
    fill,
    fee,
    feeBreakdown,
    totalCost,
    blockReason,
  };
}

function emptyFill(requestedQty: number): SimulatedFill {
  return {
    requestedQty,
    requestedQtyFp: Math.round(requestedQty * 100),
    filledQty: 0,
    filledQtyFp: 0,
    avgPrice: null,
    avgPriceFpApprox: null,
    grossCost: 0,
    grossCostMicros: 0,
    worstPrice: null,
    worstPriceFp: null,
    levels: [],
  };
}

function zeroedFill(fill: SimulatedFill): SimulatedFill {
  return {
    ...fill,
    filledQty: 0,
    filledQtyFp: 0,
    avgPrice: null,
    avgPriceFpApprox: null,
    grossCost: 0,
    grossCostMicros: 0,
    worstPrice: null,
    worstPriceFp: null,
    levels: [],
  };
}

function blocked(
  intent: PaperOrderIntent,
  latencyMs: number,
  bookReceivedEpochMs: number,
  stateKnownThroughEpochMs: number,
  bookSeq: number,
  reason: string,
  fill: SimulatedFill = emptyFill(intent.qty),
): PaperExecution {
  return result(
    intent,
    latencyMs,
    bookReceivedEpochMs,
    stateKnownThroughEpochMs,
    bookSeq,
    "blocked",
    fill,
    null,
    [],
    null,
    reason,
  );
}

export type PendingPaperOrder = {
  intent: PaperOrderIntent;
  latencyMs: number;
  executed: boolean;
};

/** Deep copy a reconstructed market state so future live deltas cannot mutate history. */
function immutableBookCopy(source: KalshiOrderBook) {
  const snap = source.snapshot();
  const copy = new KalshiOrderBook(snap.marketTicker);
  copy.applySnapshot({
    seq: snap.seq,
    exchangeTsMs: snap.exchangeTsMs,
    receivedEpochMs: snap.receivedEpochMs,
    yes: snap.yesBids.map((level) => [level.price, level.qty]),
    no: snap.noBids.map((level) => [level.price, level.qty]),
  });
  return copy;
}

/**
 * Deterministic event-time replay.
 *
 * Before applying a new book event at T, orders arriving strictly before T
 * execute against the immutable previous state. Orders arriving exactly at T
 * execute after the update (conservative tie-break). The engine deep-copies
 * every checkpoint to prevent a mutable live book from leaking future deltas.
 */
export class LatencyReplayEngine {
  private pending: PendingPaperOrder[] = [];
  private books = new Map<string, KalshiOrderBook>();
  private nowMs = Number.NEGATIVE_INFINITY;

  add(intent: PaperOrderIntent, latencies: readonly number[] = DEFAULT_LATENCY_LADDER_MS) {
    for (const latencyMs of latencies) this.pending.push({ intent, latencyMs, executed: false });
  }

  onBookUpdate(book: KalshiOrderBook, receivedEpochMs: number) {
    if (receivedEpochMs < this.nowMs) throw new Error(`REPLAY_TIME_REVERSAL ${receivedEpochMs} < ${this.nowMs}`);
    const executions = this.executeDue(receivedEpochMs, false);
    this.books.set(book.marketTicker, immutableBookCopy(book));
    this.nowMs = receivedEpochMs;
    executions.push(...this.executeDue(receivedEpochMs, true));
    return executions;
  }

  flushThrough(epochMs: number) {
    if (epochMs < this.nowMs) throw new Error(`REPLAY_TIME_REVERSAL ${epochMs} < ${this.nowMs}`);
    const executions = this.executeDue(epochMs, true);
    this.nowMs = epochMs;
    return executions;
  }

  private executeDue(knownThroughMs: number, includeEqual: boolean) {
    const executions: PaperExecution[] = [];
    for (const item of this.pending) {
      if (item.executed) continue;
      const arrival = item.intent.decisionEpochMs + item.latencyMs;
      if (includeEqual ? arrival > knownThroughMs : arrival >= knownThroughMs) continue;
      const book = this.books.get(item.intent.marketTicker);
      item.executed = true;
      if (!book) {
        executions.push(blocked(item.intent, item.latencyMs, 0, knownThroughMs, 0, "NO_BOOK_AT_ARRIVAL"));
        continue;
      }
      executions.push(executeTakerAtBook({
        intent: item.intent,
        latencyMs: item.latencyMs,
        book,
        stateKnownThroughEpochMs: knownThroughMs,
      }));
    }
    return executions;
  }

  unexecuted() {
    return this.pending.filter((x) => !x.executed);
  }
}

export const LatencyReplayQueue = LatencyReplayEngine;
