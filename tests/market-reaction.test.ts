import test from "node:test";
import assert from "node:assert/strict";
import {
  buildMarketCenter,
  inferBandWidth,
  quoteMid,
  representativeTemperature,
  strongestBucketMove,
  type MarketSeries,
} from "../lib/weather/market-reaction";

test("quoteMid prefers executable spread midpoint and falls back to last", () => {
  assert.equal(quoteMid({ yesBid: 0.4, yesAsk: 0.6, lastPrice: 0.2 }), 0.5);
  assert.equal(quoteMid({ yesBid: null, yesAsk: null, lastPrice: 0.37 }), 0.37);
});

test("band representative temperatures keep open tails adjacent to normal bucket width", () => {
  const bands = [
    { ticker: "A", label: "79 or below", lower: null, upper: 79 },
    { ticker: "B", label: "80 to 81", lower: 80, upper: 81 },
    { ticker: "C", label: "82 to 83", lower: 82, upper: 83 },
    { ticker: "D", label: "84 or above", lower: 84, upper: null },
  ];
  const width = inferBandWidth(bands);
  assert.equal(width, 2);
  assert.equal(representativeTemperature(bands[0], width), 77);
  assert.equal(representativeTemperature(bands[1], width), 80.5);
  assert.equal(representativeTemperature(bands[3], width), 86);
});

test("market center carries forward the latest quote for each bucket", () => {
  const series: MarketSeries[] = [
    {
      ticker: "LOW",
      label: "80 to 81",
      lower: 80,
      upper: 81,
      quotes: [
        { time: "2026-08-28T15:00:00Z", yesBid: 0.5, yesAsk: 0.5, lastPrice: null },
        { time: "2026-08-28T15:01:00Z", yesBid: 0.2, yesAsk: 0.2, lastPrice: null },
      ],
    },
    {
      ticker: "HIGH",
      label: "82 to 83",
      lower: 82,
      upper: 83,
      quotes: [
        { time: "2026-08-28T15:00:00Z", yesBid: 0.5, yesAsk: 0.5, lastPrice: null },
      ],
    },
  ];
  const center = buildMarketCenter(series);
  assert.equal(center.length, 2);
  assert.equal(center[0].value, 81.5);
  assert.ok(center[1].value > 81.5);
});

test("strongestBucketMove identifies the largest repricing after a shock", () => {
  const series: MarketSeries[] = [
    {
      ticker: "A",
      label: "80 to 81",
      lower: 80,
      upper: 81,
      quotes: [
        { time: "2026-08-28T15:00:00Z", yesBid: 0.3, yesAsk: 0.3, lastPrice: null },
        { time: "2026-08-28T15:15:00Z", yesBid: 0.5, yesAsk: 0.5, lastPrice: null },
      ],
    },
    {
      ticker: "B",
      label: "82 to 83",
      lower: 82,
      upper: 83,
      quotes: [
        { time: "2026-08-28T15:00:00Z", yesBid: 0.4, yesAsk: 0.4, lastPrice: null },
        { time: "2026-08-28T15:15:00Z", yesBid: 0.05, yesAsk: 0.05, lastPrice: null },
      ],
    },
  ];
  const move = strongestBucketMove(series, Date.parse("2026-08-28T15:00:00Z"), Date.parse("2026-08-28T15:15:00Z"));
  assert.equal(move?.ticker, "B");
  assert.ok(Math.abs((move?.delta ?? 0) + 0.35) < 1e-9);
});
