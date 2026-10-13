import assert from "node:assert/strict";
import test from "node:test";
import { clearSkyWatts, decodePixel, describeSolar, historyPoints, replayDay, scanAngles, scanTime, SOLAR_STATIONS, sunCosine, type SolarSample, type SolarStation } from "../lib/weather/solar-model";
const lax: SolarStation = { stid: "KLAX", lat: 33.93806, lon: -118.38889, timezone: "America/Los_Angeles", satellite: 18 };
function sample(time: string, watts: number | null): SolarSample {
  return { time, scanStart: time, scanEnd: time, publishedAt: time, sourceUrl: "https://noaa-goes18.s3.amazonaws.com/example.nc", watts, nearbyWatts: watts, validPixels: watts === null ? 0 : 9 };
}

test("both satellite views cover every supported city", () => {
  assert.deepEqual(SOLAR_STATIONS.map(s => [s.stid, s.satellite]), [["KNYC",19],["KPHL",19],["KLAX",18],["KDEN",18],["KSEA",18]]);
});
test("solar geometry matches the independently calculated Oct 12 LAX sun decline", () => {
  const first = sunCosine(new Date("2026-10-12T21:05:05Z"), lax.lat, lax.lon);
  const last = sunCosine(new Date("2026-10-12T21:35:05Z"), lax.lat, lax.lon);
  assert.ok(first > 0.6 && first < 0.7);
  assert.ok(Math.abs(last / first - 0.9343) < 0.002);
  assert.ok(clearSkyWatts(new Date("2026-10-12T21:05:05Z"), lax.lat, lax.lon) > 600);
});
test("GOES timestamps preserve day of year and fractional scan seconds", () => {
  assert.equal(scanTime("20262852100202"), "2026-10-12T21:00:20.200Z");
  assert.equal(scanTime("20243662359599"), "2024-12-31T23:59:59.900Z");
});
test("packed radiation applies scale and offset and excludes degraded or missing pixels", () => {
  assert.equal(decodePixel(1234, 0, 0.5, -10, 65535), 607);
  assert.equal(decodePixel(1234, 1, 0.5, -10, 65535), null);
  assert.equal(decodePixel(65535, 0, 0.5, -10, 65535), null);
});
test("geolocation lands on the documented airport sensor pixel", () => {
  const angle = scanAngles(lax.lat, lax.lon, -137);
  assert.equal(Math.round((angle.x + 0.151844) / 0.000056), 3520);
  assert.equal(Math.round((angle.y - 0.151844) / -0.000056), 1014);
});
test("night mode discards a daytime gauge even if its old feed is bright", () => {
  const r = describeSolar(lax, new Date("2026-10-13T02:00:00Z"), sample("2026-10-12T21:35:05Z", 502), null);
  assert.equal(r.state, "night");
  assert.equal(r.watts, 0);
  assert.equal(r.clearSkyPercent, null);
});
test("missing and old data cannot turn into a live zero or a warming signal", () => {
  const now = new Date("2026-10-12T21:50:00Z");
  assert.equal(describeSolar(lax, now, sample("2026-10-12T21:35:05Z", null), null).state, "unavailable");
  const old = describeSolar(lax, now, sample("2026-10-12T20:35:05Z", 500), null);
  assert.equal(old.state, "stale");
  assert.equal(old.trend, "unknown");
});
test("Oct 12 sunlight recovery is large even after removing normal sun decline", () => {
  const r = describeSolar(lax, new Date("2026-10-12T21:50:00Z"), sample("2026-10-12T21:35:05Z", 502.42), sample("2026-10-12T21:05:05Z", 226.2704));
  assert.equal(r.state, "live");
  assert.equal(r.trend, "rising");
  assert.ok((r.trendPoints ?? 0) > 40);
  assert.equal(Math.round(r.trendWatts ?? 0), 276);
});
test("clear skies close to sunset have little heat and no unstable ratio", () => {
  const t = "2026-10-13T01:05:00Z";
  const r = describeSolar(lax, new Date("2026-10-13T01:15:00Z"), sample(t, 50), null);
  assert.equal(r.strength, "Little");
  assert.equal(r.clearSkyPercent, null);
});
test("timeline retains missing readings, orders real timestamps and compares each point with its own sun angle", () => {
  const points = historyPoints(lax,[sample("2026-10-12T21:35:05Z",502.42),sample("2026-10-12T21:05:05Z",226.27),sample("2026-10-12T21:15:05Z",null)]);
  assert.equal(points.length,3);
  assert.equal(points[0]?.time,"2026-10-12T21:05:05Z");
  assert.equal(points[1]?.watts,null);
  assert.equal(points[1]?.clearSkyPercent,null);
  assert.ok(points[0].clearSkyWatts > points[2].clearSkyWatts);
  assert.ok(points[2].clearSkyPercent! > points[0].clearSkyPercent! + 40);
});
test("before sunrise replay keeps the last daylight date rather than an empty new day", () => {
  assert.equal(replayDay(lax,new Date("2026-10-13T09:00:00Z")),"2026-10-12");
  assert.equal(replayDay(lax,new Date("2026-10-13T00:00:00Z")),"2026-10-12");
  assert.equal(replayDay(lax,new Date("2026-10-13T19:00:00Z")),"2026-10-13");
});
