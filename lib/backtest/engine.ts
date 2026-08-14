import type {
  ContractBand,
  MechanicalSignal,
  ObservationPoint,
  QuotePoint,
} from "../types";

export type EngineConfig = {
  quoteToleranceSeconds: number;
  reactedYesAskThreshold: number;
  requireSettlementCompatible: boolean;
};

export const DEFAULT_ENGINE_CONFIG: EngineConfig = {
  quoteToleranceSeconds: 90,
  reactedYesAskThreshold: 0.05,
  requireSettlementCompatible: true,
};

function epoch(value: string) {
  return new Date(value).getTime();
}

function firstQuoteAtOrAfter(
  quotes: QuotePoint[],
  at: string,
  toleranceSeconds: number,
) {
  const start = epoch(at);
  return quotes.find((quote) => {
    const delta = epoch(quote.capturedAt) - start;
    return delta >= 0 && delta <= toleranceSeconds * 1000;
  });
}

function firstReaction(quotes: QuotePoint[], at: string, threshold: number) {
  const start = epoch(at);
  return quotes.find(
    (quote) => epoch(quote.capturedAt) >= start && quote.yesAsk !== null && quote.yesAsk <= threshold,
  );
}

export function runMechanicalLatencyBacktest(
  station: string,
  contracts: ContractBand[],
  observations: ObservationPoint[],
  quotes: QuotePoint[],
  config: EngineConfig = DEFAULT_ENGINE_CONFIG,
): MechanicalSignal[] {
  const eligible = observations
    .filter((point) => !config.requireSettlementCompatible || point.settlementCompatible)
    .sort((a, b) => epoch(a.receivedAt) - epoch(b.receivedAt));
  const quotesByContract = new Map<string, QuotePoint[]>();
  for (const quote of [...quotes].sort((a, b) => epoch(a.capturedAt) - epoch(b.capturedAt))) {
    const list = quotesByContract.get(quote.contractTicker) ?? [];
    list.push(quote);
    quotesByContract.set(quote.contractTicker, list);
  }

  const signaled = new Set<string>();
  const signals: MechanicalSignal[] = [];
  let runningHigh = -Infinity;

  for (const point of eligible) {
    runningHigh = Math.max(runningHigh, point.temperatureF);
    // The settlement report is whole-degree Fahrenheit. A compatible ASOS
    // five-minute/routine value conservatively establishes the rounded degree.
    const officialFloor = Math.round(runningHigh);

    for (const contract of contracts) {
      if (contract.upper === null || contract.upper >= officialFloor || signaled.has(contract.ticker)) continue;
      const contractQuotes = quotesByContract.get(contract.ticker) ?? [];
      const entry = firstQuoteAtOrAfter(contractQuotes, point.receivedAt, config.quoteToleranceSeconds);
      const reaction = firstReaction(contractQuotes, point.receivedAt, config.reactedYesAskThreshold);
      const noAsk = entry?.yesBid === null || entry?.yesBid === undefined ? null : 1 - entry.yesBid;
      const lag = reaction ? Math.round((epoch(reaction.capturedAt) - epoch(point.receivedAt)) / 1000) : null;
      const hasOriginalReceipt = point.receiptQuality === "actual";

      signals.push({
        station,
        contractTicker: contract.ticker,
        triggeredAt: point.receivedAt,
        triggerObservedAt: point.observedAt,
        runningHighF: runningHigh,
        officialFloorF: officialFloor,
        edgeClass: "market_repricing",
        action: "BUY_NO",
        entryPrice: noAsk,
        quoteCapturedAt: entry?.capturedAt ?? null,
        reactionAt: reaction?.capturedAt ?? null,
        reactionLagSeconds: lag,
        grossProfitPerContract: noAsk === null ? null : 1 - noAsk,
        executableProxy: Boolean(entry && hasOriginalReceipt),
        limitation: entry
          ? hasOriginalReceipt
            ? "Minute candle quote; historical size and queue position are unknown."
            : "Original source receipt timestamp is not preserved."
          : "No quote inside the configured tolerance window.",
      });
      signaled.add(contract.ticker);
    }
  }
  return signals;
}
