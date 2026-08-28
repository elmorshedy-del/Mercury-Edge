export type MarketBand = {
  ticker: string;
  label: string;
  lower: number | null;
  upper: number | null;
};

export type MarketQuote = {
  time: string;
  yesBid: number | null;
  yesAsk: number | null;
  lastPrice: number | null;
};

export type MarketSeries = MarketBand & {
  quotes: MarketQuote[];
};

export type MarketCenterPoint = {
  time: string;
  value: number;
  probabilityMass: number;
  bucketCount: number;
};

export function quoteMid(quote: Pick<MarketQuote, "yesBid" | "yesAsk" | "lastPrice">) {
  if (quote.yesBid !== null && quote.yesAsk !== null) return (quote.yesBid + quote.yesAsk) / 2;
  if (quote.lastPrice !== null) return quote.lastPrice;
  if (quote.yesBid !== null) return quote.yesBid;
  if (quote.yesAsk !== null) return quote.yesAsk;
  return null;
}

export function inferBandWidth(bands: MarketBand[]) {
  const widths = bands
    .filter((band) => band.lower !== null && band.upper !== null)
    .map((band) => (band.upper as number) - (band.lower as number) + 1)
    .filter((width) => Number.isFinite(width) && width > 0)
    .sort((a, b) => a - b);
  if (!widths.length) return 2;
  return widths[Math.floor(widths.length / 2)];
}

export function representativeTemperature(band: MarketBand, width: number) {
  if (band.lower !== null && band.upper !== null) return (band.lower + band.upper) / 2;
  if (band.upper !== null) return band.upper - width;
  if (band.lower !== null) return band.lower + width;
  return null;
}

export function buildMarketCenter(series: MarketSeries[]): MarketCenterPoint[] {
  if (!series.length) return [];
  const width = inferBandWidth(series);
  const byMinute = new Map<number, Array<{ ticker: string; quote: MarketQuote }>>();
  const bandByTicker = new Map(series.map((item) => [item.ticker, item]));

  for (const item of series) {
    for (const quote of item.quotes) {
      const minute = Math.floor(new Date(quote.time).getTime() / 60_000) * 60_000;
      if (!Number.isFinite(minute)) continue;
      const list = byMinute.get(minute) ?? [];
      list.push({ ticker: item.ticker, quote });
      byMinute.set(minute, list);
    }
  }

  const state = new Map<string, number>();
  const output: MarketCenterPoint[] = [];
  for (const minute of [...byMinute.keys()].sort((a, b) => a - b)) {
    for (const update of byMinute.get(minute) ?? []) {
      const mid = quoteMid(update.quote);
      if (mid !== null && Number.isFinite(mid)) state.set(update.ticker, Math.max(0, Math.min(1, mid)));
    }

    let weighted = 0;
    let mass = 0;
    let bucketCount = 0;
    for (const [ticker, probability] of state) {
      const band = bandByTicker.get(ticker);
      if (!band) continue;
      const center = representativeTemperature(band, width);
      if (center === null) continue;
      weighted += center * probability;
      mass += probability;
      bucketCount += 1;
    }
    if (mass >= 0.2 && bucketCount >= 2) {
      output.push({
        time: new Date(minute).toISOString(),
        value: weighted / mass,
        probabilityMass: mass,
        bucketCount,
      });
    }
  }
  return output;
}

export function pointAtOrBefore<T extends { time: string }>(points: T[], timeMs: number) {
  let best: T | null = null;
  for (const point of points) {
    const ms = new Date(point.time).getTime();
    if (!Number.isFinite(ms) || ms > timeMs) continue;
    if (!best || ms > new Date(best.time).getTime()) best = point;
  }
  return best;
}

export function pointNear<T extends { time: string }>(points: T[], timeMs: number, toleranceMs = 3 * 60_000) {
  let best: T | null = null;
  let bestDistance = Infinity;
  for (const point of points) {
    const ms = new Date(point.time).getTime();
    const distance = Math.abs(ms - timeMs);
    if (distance <= toleranceMs && distance < bestDistance) {
      best = point;
      bestDistance = distance;
    }
  }
  return best;
}

export function strongestBucketMove(series: MarketSeries[], fromMs: number, toMs: number) {
  let best: { ticker: string; label: string; delta: number } | null = null;
  for (const item of series) {
    const before = pointAtOrBefore(item.quotes, fromMs);
    const after = pointNear(item.quotes, toMs, 4 * 60_000) ?? pointAtOrBefore(item.quotes, toMs);
    if (!before || !after) continue;
    const p0 = quoteMid(before);
    const p1 = quoteMid(after);
    if (p0 === null || p1 === null) continue;
    const delta = p1 - p0;
    if (!best || Math.abs(delta) > Math.abs(best.delta)) best = { ticker: item.ticker, label: item.label, delta };
  }
  return best;
}
