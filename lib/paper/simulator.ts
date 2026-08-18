import { predictionTakerFee, type FeeType } from "./fees";
import { consumeAsTaker, type BookSide, type KalshiOrderBook, type SimulatedFill } from "./orderbook";

export const DEFAULT_LATENCY_LADDER_MS = [0, 50, 100, 250, 500, 1_000, 2_000, 5_000] as const;

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
  linearCent: boolean;
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
  bookSeq: number;
  status: "filled" | "partial" | "unfilled" | "blocked";
  fill: SimulatedFill;
  fee: number | null;
  totalCost: number | null;
  blockReason: string | null;
};

export function executeTakerAtBook(input: {
  intent: PaperOrderIntent;
  latencyMs: number;
  book: KalshiOrderBook;
  bookReceivedEpochMs: number;
}): PaperExecution {
  const { intent, latencyMs, book, bookReceivedEpochMs } = input;
  const arrivalEpochMs = intent.decisionEpochMs + latencyMs;
  const snap = book.snapshot();

  if (bookReceivedEpochMs < arrivalEpochMs) {
    return blocked(intent, latencyMs, bookReceivedEpochMs, snap.seq, "BOOK_PRECEDES_SIMULATED_ARRIVAL");
  }

  const fill = consumeAsTaker(book, intent.outcome, intent.qty, intent.maxPrice);
  if (intent.timeInForce === "fok" && fill.filledQty + 1e-9 < intent.qty) {
    return {
      signalId: intent.signalId,
      strategyCode: intent.strategyCode,
      marketTicker: intent.marketTicker,
      outcome: intent.outcome,
      latencyMs,
      decisionEpochMs: intent.decisionEpochMs,
      arrivalEpochMs,
      bookReceivedEpochMs,
      bookSeq: snap.seq,
      status: "unfilled",
      fill: { ...fill, filledQty: 0, avgPrice: null, grossCost: 0, worstPrice: null, levels: [] },
      fee: 0,
      totalCost: 0,
      blockReason: null,
    };
  }

  if (fill.filledQty <= 1e-9 || fill.avgPrice === null) {
    return {
      signalId: intent.signalId,
      strategyCode: intent.strategyCode,
      marketTicker: intent.marketTicker,
      outcome: intent.outcome,
      latencyMs,
      decisionEpochMs: intent.decisionEpochMs,
      arrivalEpochMs,
      bookReceivedEpochMs,
      bookSeq: snap.seq,
      status: "unfilled",
      fill,
      fee: 0,
      totalCost: 0,
      blockReason: null,
    };
  }

  const feeQuote = predictionTakerFee({
    feeType: intent.feeType,
    feeMultiplier: intent.feeMultiplier,
    contracts: fill.filledQty,
    price: fill.avgPrice,
    linearCent: intent.linearCent,
  });
  if (!feeQuote.supported || feeQuote.cashFeeUpperBound === null) {
    return blocked(intent, latencyMs, bookReceivedEpochMs, snap.seq, `FEE_MODEL_BLOCKED:${feeQuote.reason ?? "UNKNOWN"}`, fill);
  }

  return {
    signalId: intent.signalId,
    strategyCode: intent.strategyCode,
    marketTicker: intent.marketTicker,
    outcome: intent.outcome,
    latencyMs,
    decisionEpochMs: intent.decisionEpochMs,
    arrivalEpochMs,
    bookReceivedEpochMs,
    bookSeq: snap.seq,
    status: fill.filledQty + 1e-9 >= intent.qty ? "filled" : "partial",
    fill,
    fee: feeQuote.cashFeeUpperBound,
    totalCost: fill.grossCost + feeQuote.cashFeeUpperBound,
    blockReason: null,
  };
}

function blocked(
  intent: PaperOrderIntent,
  latencyMs: number,
  bookReceivedEpochMs: number,
  bookSeq: number,
  reason: string,
  fill: SimulatedFill = { requestedQty: intent.qty, filledQty: 0, avgPrice: null, grossCost: 0, worstPrice: null, levels: [] },
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
    bookSeq,
    status: "blocked",
    fill,
    fee: null,
    totalCost: null,
    blockReason: reason,
  };
}

export type PendingPaperOrder = {
  intent: PaperOrderIntent;
  latencyMs: number;
  executed: boolean;
};

/**
 * Deterministic replay helper. Feed reconstructed books in received-time order.
 * A pending order executes on the first book state received at or after its
 * simulated arrival time. This prevents using a later minute close as though it
 * existed at the signal instant.
 */
export class LatencyReplayQueue {
  private pending: PendingPaperOrder[] = [];

  add(intent: PaperOrderIntent, latencies: readonly number[] = DEFAULT_LATENCY_LADDER_MS) {
    for (const latencyMs of latencies) this.pending.push({ intent, latencyMs, executed: false });
  }

  onBook(book: KalshiOrderBook, receivedEpochMs: number) {
    const executions: PaperExecution[] = [];
    for (const item of this.pending) {
      if (item.executed) continue;
      if (item.intent.marketTicker !== book.marketTicker) continue;
      if (receivedEpochMs < item.intent.decisionEpochMs + item.latencyMs) continue;
      item.executed = true;
      executions.push(executeTakerAtBook({ intent: item.intent, latencyMs: item.latencyMs, book, bookReceivedEpochMs: receivedEpochMs }));
    }
    return executions;
  }

  unexecuted() {
    return this.pending.filter((x) => !x.executed);
  }
}
