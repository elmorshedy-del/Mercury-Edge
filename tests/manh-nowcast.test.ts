import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { predictProba, type ExportedBooster } from "../lib/weather/gbm";
import {
  applyPlatt,
  buildAirportData,
  buildGrid,
  climateDayStart,
  dayFeatures,
  interpolateInside,
  nextMetarFeatures,
  parseKnycMetar,
  predictFinalHigh,
  type AirportHfObservation,
  type AirportMetar,
  type KnycReport,
  type MesonetObservation,
  type NbmRun,
  type NowcastModel,
} from "../lib/weather/manh-nowcast";

const model = JSON.parse(readFileSync(new URL("../lib/weather/manh-nowcast-model.json", import.meta.url), "utf8")) as NowcastModel;

type FeatureRow = { features: Record<string, number | null> };
type DayFixture = {
  start: number;
  end: number;
  observations: MesonetObservation[];
  reports: Array<KnycReport & { raw: string }>;
  nbm: NbmRun[];
  next_metar: Array<FeatureRow & { t0: number; tau: number }>;
  day_rows: Array<FeatureRow & { tau: number }>;
  airport_hf: AirportHfObservation[];
  airport_metars: AirportMetar[];
};

function assertClose(actual: number, expected: number | null, label: string) {
  if (expected === null) {
    assert.ok(!Number.isFinite(actual), `${label}: expected missing, got ${actual}`);
    return;
  }
  assert.ok(Math.abs(actual - expected) <= 1e-6 * Math.max(1, Math.abs(expected)), `${label}: ${actual} vs ${expected}`);
}

test("tree evaluator reproduces LightGBM probabilities", () => {
  const fixture = JSON.parse(readFileSync(new URL("./fixtures/manh-nowcast-gbm.json", import.meta.url), "utf8")) as Record<
    string,
    { rows: Array<Array<number | null>>; probs: number[][] }
  >;
  for (const name of ["next_metar", "hidden_max", "final_high"] as const) {
    const booster = model[name] as ExportedBooster;
    fixture[name].rows.forEach((row, index) => {
      const probs = predictProba(booster, row.map((value) => (value === null ? Number.NaN : value)));
      probs.forEach((p, k) => assertClose(p, fixture[name].probs[index][k], `${name}[${index}][${k}]`));
    });
  }
});

test("Platt step recalibrates P(already higher) and keeps a valid distribution", () => {
  const probs = [0.55, 0.3, 0.1, 0.05];
  const out = applyPlatt(probs, [0.9583, -0.1828]);
  const z = Math.log(0.45 / 0.55);
  assertClose(1 - out[0], 1 / (1 + Math.exp(-(0.9583 * z - 0.1828))), "P(>=1)");
  assertClose(out.reduce((a, b) => a + b, 0), 1, "sum");
  assertClose(out[1] / out[2], 3, "shape above +0 preserved");
  assert.deepEqual(applyPlatt(probs, null), probs);
});

test("interpolation matches pandas limit=2 inside fills", () => {
  const out = interpolateInside([1, Number.NaN, 3, Number.NaN, Number.NaN, Number.NaN, 7, Number.NaN]);
  assert.deepEqual(Array.from(out).map((v) => (Number.isNaN(v) ? null : v)), [1, 2, 3, 4, 5, null, 7, null]);
});

test("METAR parser recovers whole-degree F from the T-group and the 6-hour max", () => {
  const report = parseKnycMetar(
    "METAR KNYC 201551Z AUTO 11003KT 10SM FEW045 BKN055 29/19 A2992 RMK AO2 SLP131 T02890189 10289 20250 $",
    Date.parse("2026-08-20T15:51:00Z"),
    29,
  );
  assert.equal(report?.temp, 84);
  assert.equal(report?.max6, 84);
  assert.equal(report?.routine, true);
});

test("climate day uses local standard time", () => {
  assert.equal(climateDayStart(Date.parse("2026-07-14T05:00:00Z")), Date.parse("2026-07-13T05:00:00Z"));
  assert.equal(climateDayStart(Date.parse("2026-07-14T05:05:00Z")), Date.parse("2026-07-14T05:00:00Z"));
});

test("live feature engine replays build.py exactly for a historical day", () => {
  const fixture = JSON.parse(readFileSync(new URL("./fixtures/manh-nowcast-day.json", import.meta.url), "utf8")) as DayFixture;
  const grid = buildGrid(fixture.observations, model.stations, fixture.start, fixture.end);
  const air = buildAirportData(model, fixture.airport_hf, fixture.airport_metars, fixture.start, fixture.end);
  assert.ok(air, "model should carry the airport configuration");
  const reports: KnycReport[] = fixture.reports.map((report) => ({ ...report, max6: report.max6 ?? null }));
  assert.ok(fixture.next_metar.length > 200 && fixture.day_rows.length > 200);
  for (const row of fixture.next_metar) {
    const features = nextMetarFeatures(model, grid, reports, row.t0, row.tau, air);
    assert.ok(features, `features at ${new Date(row.tau).toISOString()}`);
    for (const [name, expected] of Object.entries(row.features)) {
      assertClose(features[name], expected, `A ${new Date(row.tau).toISOString()} ${name}`);
    }
  }
  for (const row of fixture.day_rows) {
    const result = dayFeatures(model, grid, reports, fixture.nbm, row.tau, air);
    assert.ok(result, `day features at ${new Date(row.tau).toISOString()}`);
    for (const [name, expected] of Object.entries(row.features)) {
      assertClose(result.features[name], expected, `B ${new Date(row.tau).toISOString()} ${name}`);
    }
    const dist = predictFinalHigh(model, result.features, result.knownMax);
    assert.ok(Math.abs(dist.reduce((sum, item) => sum + item.p, 0) - 1) < 1e-9);
    assert.ok(dist.every((item) => item.value >= result.knownMax));
  }
});
