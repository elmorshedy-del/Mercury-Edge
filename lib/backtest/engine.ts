import { predictionTakerFeesForFills } from "../paper/fees";
import type {
  ContractBand,
  HardTriggerKind,
  MarketTrade,
  MechanicalSignal,
  ObservationPoint,
  QuotePoint,
} from "../types";

export type EngineConfig = {
  quoteToleranceSeconds: number;
  reactionWindowSeconds: number;
  violenceWindowSeconds: number;
  reactedYesAskThreshold: number;
  violentMinPreYesBid: number;
  violentMinBidCollapse: number;
  minimumTapeGrossEdge: number;
  assumedDecisionLatencyMs: number;
  feeMultiplier: number;
  requireSettlementCompatible: boolean;
};

export const DEFAULT_ENGINE_CONFIG: EngineConfig = {
  quoteToleranceSeconds: 90,
  reactionWindowSeconds: 60 * 60,
  violenceWindowSeconds: 15 * 60,
  reactedYesAskThreshold: 0.05,
  violentMinPreYesBid: 0.2,
  violentMinBidCollapse: 0.25,
  minimumTapeGrossEdge: 0.1,
  assumedDecisionLatencyMs: 1_000,
  feeMultiplier: 1,
  // Keep unverified/observation-only rows for discovery, but never promote them
  // to a hard-state or executable result.
  requireSettlementCompatible: false,
};

function epoch(value: string) {
  return new Date(value).getTime();
}

function alignmentTime(point: ObservationPoint) {
  return point.receiptQuality === "discovery_only" ? point.observedAt : point.receivedAt;
}

function quoteMid(quote: QuotePoint | undefined) {
  if (!quote) return null;
  if (quote.yesBid !== null && quote.yesAsk !== null) return (quote.yesBid + quote.yesAsk) / 2;
  return quote.lastPrice ?? quote.yesBid ?? quote.yesAsk ?? null;
}

function lastQuoteAtOrBefore(quotes: QuotePoint[], at: string, toleranceSeconds: number) {
  const cutoff = epoch(at);
  let found: QuotePoint | undefined;
  for (const quote of quotes) {
    if (epoch(quote.capturedAt) > cutoff) break;
    found = quote;
  }
  return found && cutoff - epoch(found.capturedAt) <= toleranceSeconds * 1000 ? found : undefined;
}

function firstQuoteAtOrAfter(
  quotes: QuotePoint[],
  at: string,
  toleranceSeconds: number,
  assumedDecisionLatencyMs: number,
) {
  const start = epoch(at) + assumedDecisionLatencyMs;
  return quotes.find((quote) => {
    const delta = epoch(quote.capturedAt) - start;
    return delta >= 0 && delta <= toleranceSeconds * 1000;
  });
}

function firstReaction(
  quotes: QuotePoint[],
  at: string,
  threshold: number,
  windowSeconds: number,
) {
  const start = epoch(at);
  const end = start + windowSeconds * 1000;
  return quotes.find((quote) => {
    const quoteAt = epoch(quote.capturedAt);
    const low = quote.yesAskLow ?? quote.yesAsk;
    const whollyAfterTrigger = quote.sourcePrecision === "l2" || quoteAt - 60_000 >= start;
    return quoteAt >= start && quoteAt <= end && whollyAfterTrigger && low !== null && low <= threshold;
  });
}

function latencyInterval(reaction: QuotePoint | undefined, at: string) {
  if (!reaction) return { lower: null, upper: null };
  const upper = Math.max(0, (epoch(reaction.capturedAt) - epoch(at)) / 1000);
  if (reaction.sourcePrecision === "l2") return { lower: upper, upper };
  return { lower: Math.max(0, upper - 60), upper };
}

function triggerValue(point: ObservationPoint) {
  if (point.maxTemperatureF !== null && point.maxTemperatureF !== undefined && point.maxTemperatureF >= point.temperatureF) {
    return {
      value: point.maxTemperatureF,
      kind: point.maxTemperatureKind ?? "metar_6h_max" as HardTriggerKind,
    };
  }
  if (point.reportType === "SPECI") return { value: point.temperatureF, kind: "speci_current_temp" as const };
  if (point.reportType === "OMO") return { value: point.temperatureF, kind: "omo_precise_temp" as const };
  if (point.reportType === "METAR") {
    return {
      value: point.temperatureF,
      kind: point.rawText && /(?:^|\s)T[01]\d{3}[01]\d{3}(?:\s|$)/.test(point.rawText)
        ? "metar_current_temp" as const
        : "normalized_current_temp" as const,
    };
  }
  return { value: point.temperatureF, kind: "normalized_current_temp" as const };
}

function minimumPostBid(quotes: QuotePoint[], at: string, windowSeconds: number) {
  const start = epoch(at);
  const end = start + windowSeconds * 1000;
  const values = quotes
    .filter((quote) => {
      const quoteAt = epoch(quote.capturedAt);
      const whollyAfterTrigger = quote.sourcePrecision === "l2" || quoteAt - 60_000 >= start;
      return quoteAt >= start && quoteAt <= end && whollyAfterTrigger;
    })
    .map((quote) => quote.yesBidLow ?? quote.yesBid)
    .filter((value): value is number => value !== null && Number.isFinite(value));
  return values.length ? Math.min(...values) : null;
}

function observedNoTakerTape(
  trades: MarketTrade[],
  contract: ContractBand,
  triggerAt: string,
  config: EngineConfig,
) {
  const start = epoch(triggerAt) + config.assumedDecisionLatencyMs;
  const end = epoch(triggerAt) + config.violenceWindowSeconds * 1000;
  const qualifying = trades.filter((trade) =>
    trade.contractTicker === contract.ticker
    && !trade.isBlockTrade
    && trade.takerOutcomeSide === "no"
    && epoch(trade.createdAt) >= start
    && epoch(trade.createdAt) <= end
    && 1 - trade.noPrice >= config.minimumTapeGrossEdge,
  );
  const quantity = qualifying.reduce((sum, trade) => sum + trade.quantity, 0);
  if (quantity <= 0) return { quantity: 0, vwap: null, counterfactualNet: null };
  const cost = qualifying.reduce((sum, trade) => sum + trade.noPrice * trade.quantity, 0);
  const vwap = cost / quantity;
  const fees = predictionTakerFeesForFills({
    feeType: "quadratic",
    feeMultiplier: config.feeMultiplier,
    fills: qualifying.map((trade) => ({
      priceFp: Math.round(trade.noPrice * 10_000),
      qtyFp: Math.round(trade.quantity * 100),
    })),
  });
  const gross = contract.result === "no" ? quantity - cost : contract.result === "yes" ? -cost : null;
  return {
    quantity,
    vwap,
    counterfactualNet: gross === null || fees.netFee === null ? null : gross - fees.netFee,
  };
}

function limitationFor(input: {
  point: ObservationPoint;
  hardStateProven: boolean;
  entry: QuotePoint | undefined;
  tapeQuantity: number;
  feeMultiplier: number;
}) {
  if (input.point.receiptQuality === "discovery_only") {
    return "Archive preserves observation time but not original public receipt; alignment is diagnostic and no executable latency is claimed.";
  }
  if (!input.hardStateProven) {
    return "The report-to-settlement transformation is unverified; this is a candidate elimination, not a mathematically certified dead bucket.";
  }
  if (input.point.receiptQuality !== "actual") {
    return "The report has no exact first-public receipt timestamp; price movement can be studied, but decision latency and counterfactual profit are withheld.";
  }
  if (input.tapeQuantity > 0) {
    return `Public tape proves another taker bought NO at the recorded price and size; it does not prove a counterfactual order would win the same liquidity. Fees use configured quadratic multiplier ${input.feeMultiplier}, not an exchange invoice.`;
  }
  if (input.entry) {
    return "Minute-candle close is a price proxy; historical depth, cancellations, queue state, and a fill are not observable.";
  }
  return "No post-trigger quote inside the configured tolerance; this may be an empty book or an unrecorded resting-book state.";
}

export function runMechanicalLatencyBacktest(
  station: string,
  contracts: ContractBand[],
  observations: ObservationPoint[],
  quotes: QuotePoint[],
  config: EngineConfig = DEFAULT_ENGINE_CONFIG,
  trades: MarketTrade[] = [],
): MechanicalSignal[] {
  const eligible = observations
    .filter((point) => !config.requireSettlementCompatible || point.settlementCompatible)
    .sort((a, b) => epoch(alignmentTime(a)) - epoch(alignmentTime(b)));
  const quotesByContract = new Map<string, QuotePoint[]>();
  for (const quote of [...quotes].sort((a, b) => epoch(a.capturedAt) - epoch(b.capturedAt))) {
    const list = quotesByContract.get(quote.contractTicker) ?? [];
    list.push(quote);
    quotesByContract.set(quote.contractTicker, list);
  }

  const candidateSignaled = new Set<string>();
  const provenSignaled = new Set<string>();
  const signals: MechanicalSignal[] = [];
  let discoveryRunningHigh = Number.NEGATIVE_INFINITY;
  let provenRunningHigh = Number.NEGATIVE_INFINITY;

  for (const point of eligible) {
    const trigger = triggerValue(point);
    const priorDiscoveryFloor = Math.round(discoveryRunningHigh);
    const priorProvenFloor = Math.round(provenRunningHigh);
    discoveryRunningHigh = Math.max(discoveryRunningHigh, trigger.value);
    if (point.settlementCompatible) provenRunningHigh = Math.max(provenRunningHigh, trigger.value);
    const hardStateProven = point.settlementCompatible;
    const runningHigh = hardStateProven ? provenRunningHigh : discoveryRunningHigh;
    const officialFloor = Math.round(runningHigh);
    const priorFloor = hardStateProven ? priorProvenFloor : priorDiscoveryFloor;
    // A report is a trigger only when it newly makes at least one whole-degree
    // outcome impossible in its evidence lane.
    if (officialFloor <= priorFloor) continue;
    const signaled = hardStateProven ? provenSignaled : candidateSignaled;
    const triggeredAt = alignmentTime(point);

    for (const contract of contracts) {
      if (
        contract.upper === null
        || contract.upper >= officialFloor
        || signaled.has(contract.ticker)
        || (!hardStateProven && provenSignaled.has(contract.ticker))
      ) continue;
      const contractQuotes = quotesByContract.get(contract.ticker) ?? [];
      const pre = lastQuoteAtOrBefore(contractQuotes, triggeredAt, config.quoteToleranceSeconds);
      const entry = firstQuoteAtOrAfter(
        contractQuotes,
        triggeredAt,
        config.quoteToleranceSeconds,
        config.assumedDecisionLatencyMs,
      );
      const reaction = firstReaction(
        contractQuotes,
        triggeredAt,
        config.reactedYesAskThreshold,
        config.reactionWindowSeconds,
      );
      const noAsk = entry?.yesBid === null || entry?.yesBid === undefined ? null : 1 - entry.yesBid;
      const interval = latencyInterval(reaction, triggeredAt);
      const preBid = pre?.yesBid ?? null;
      const postLowBid = minimumPostBid(contractQuotes, triggeredAt, config.violenceWindowSeconds);
      const violentMoveCents = preBid === null || postLowBid === null
        ? null
        : Math.max(0, (preBid - postLowBid) * 100);
      const violent = preBid !== null
        && preBid >= config.violentMinPreYesBid
        && violentMoveCents !== null
        && violentMoveCents >= config.violentMinBidCollapse * 100;
      const profitEligible = hardStateProven && point.receiptQuality === "actual";
      const tape = profitEligible
        ? observedNoTakerTape(trades, contract, triggeredAt, config)
        : { quantity: 0, vwap: null, counterfactualNet: null };
      const candidateProxy = Boolean(
        entry
        && point.receiptQuality === "actual"
        && hardStateProven
        && noAsk !== null
        && noAsk < 1,
      );
      const evidenceTier = tape.quantity > 0
        ? "trade_tape_observed" as const
        : entry
          ? "minute_candle_proxy" as const
          : "weather_only" as const;
      const profitStatus = tape.quantity > 0
        ? "counterfactual_tape" as const
        : candidateProxy
          ? "hypothetical_one_contract" as const
          : "none" as const;

      signals.push({
        station,
        contractTicker: contract.ticker,
        triggeredAt,
        triggerObservedAt: point.observedAt,
        alignmentClock: point.receiptQuality === "discovery_only" ? "observation_time_only" : "source_receipt",
        triggerSource: point.source,
        triggerReportType: point.reportType,
        triggerKind: trigger.kind,
        triggerReceiptQuality: point.receiptQuality,
        triggerRawText: point.rawText ?? null,
        runningHighF: runningHigh,
        officialFloorF: officialFloor,
        edgeClass: "market_repricing",
        action: "BUY_NO",
        entryPrice: noAsk,
        quoteCapturedAt: entry?.capturedAt ?? null,
        reactionAt: reaction?.capturedAt ?? null,
        reactionLagSeconds: interval.upper === null ? null : Math.round(interval.upper),
        reactionLagLowerSeconds: interval.lower === null ? null : Math.round(interval.lower),
        reactionLagUpperSeconds: interval.upper === null ? null : Math.round(interval.upper),
        preTriggerYesBid: preBid,
        preTriggerYesMid: quoteMid(pre),
        postWindowLowYesBid: postLowBid,
        violentMoveCents,
        violent,
        staleEdgeCents: entry?.yesBid === null || entry?.yesBid === undefined ? null : entry.yesBid * 100,
        candidateProxy,
        hardStateProven,
        evidenceTier,
        observedNoTakerQuantity: tape.quantity,
        observedNoTakerVwap: tape.vwap,
        tapeCounterfactualNetProfit: tape.counterfactualNet,
        profitStatus,
        grossProfitPerContract: candidateProxy && noAsk !== null ? 1 - noAsk : null,
        // Only a sequenced L2 replay may set this true. Candles and public
        // trades remain useful evidence, but neither proves our order filled.
        executableProxy: false,
        limitation: limitationFor({
          point,
          hardStateProven,
          entry,
          tapeQuantity: tape.quantity,
          feeMultiplier: config.feeMultiplier,
        }),
      });
      signaled.add(contract.ticker);
    }
  }
  return signals;
}
