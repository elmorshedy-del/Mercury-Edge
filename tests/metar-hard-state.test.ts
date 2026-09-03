import assert from "node:assert/strict";
import test from "node:test";
import { parseMetarHardState } from "../lib/weather/metar-hard-state";

test("parses precise temperature and six-hour maximum from raw METAR remarks", () => {
  const parsed = parseMetarHardState(
    "KLAX 022053Z 26012KT 10SM FEW010 24/18 A2994 RMK AO2 T02440178 10250=",
  );
  assert.ok(Math.abs((parsed.preciseTemperatureF ?? 0) - 75.92) < 1e-9);
  assert.equal(parsed.maximumTemperatureF, 77);
  assert.equal(parsed.maximumKind, "metar_6h_max");
});

test("prefers the cumulative 24-hour maximum when it is the highest hard-state group", () => {
  const parsed = parseMetarHardState(
    "KLAX 030453Z 00000KT 10SM CLR 21/17 A2998 RMK AO2 T02110167 10239 402780167=",
  );
  assert.ok(Math.abs((parsed.maximumTemperatureF ?? 0) - 82.04) < 1e-9);
  assert.equal(parsed.maximumKind, "metar_24h_max");
});

test("returns nulls rather than guessing from coarse slash temperatures", () => {
  assert.deepEqual(parseMetarHardState("KLAX 022053Z 26012KT 10SM FEW010 24/18 A2994"), {
    preciseTemperatureF: null,
    maximumTemperatureF: null,
    maximumKind: null,
  });
});
