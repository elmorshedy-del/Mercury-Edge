import assert from "node:assert/strict";
import test from "node:test";
import { marketToBand } from "../lib/sources/kalshi";
import { parseCli, parseDsm } from "../lib/sources/nws";
import { eventTickerForDate } from "../lib/time";

test("parses two-degree, cap, and floor market labels", () => {
  assert.deepEqual(
    marketToBand({ ticker: "A", event_ticker: "E", strike_type: "between", yes_sub_title: "85° to 86°" }),
    { ticker: "A", label: "85° to 86°", lower: 85, upper: 86, result: "unknown" },
  );
  assert.equal(
    marketToBand({ ticker: "B", event_ticker: "E", strike_type: "less", yes_sub_title: "82° or below" }).upper,
    82,
  );
  assert.equal(
    marketToBand({ ticker: "C", event_ticker: "E", strike_type: "greater", yes_sub_title: "89° or above" }).lower,
    89,
  );
});

test("parses DSM high and converts local standard time to UTC", () => {
  const product = "KNYC DS 1715 13/08 851537/";
  const parsed = parseDsm(product, "2026-08-13T21:15:00Z", "America/New_York");
  assert.equal(parsed.high, 85);
  assert.equal(parsed.highAt, "2026-08-13T20:37:00.000Z");
});

test("parses final CLI maximum", () => {
  assert.equal(parseCli("TEMPERATURE (F)\n MAXIMUM         85"), 85);
});

test("uses the station timezone when naming the event", () => {
  assert.equal(
    eventTickerForDate("KXHIGHNY", new Date("2026-08-14T02:00:00Z"), "America/New_York"),
    "KXHIGHNY-26AUG13",
  );
});
