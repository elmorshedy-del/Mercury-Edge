import assert from "node:assert/strict";
import test from "node:test";
import { validateJobParameters } from "../lib/jobs/validation";

test("accepts and normalizes a configured research range", () => {
  assert.deepEqual(
    validateJobParameters({
      from: "2026-08-01",
      to: "2026-08-13",
      stations: ["knyc", "KPHL"],
    }),
    { from: "2026-08-01", to: "2026-08-13", stations: ["KNYC", "KPHL"] },
  );
});

test("rejects reversed, oversized, duplicate, and unknown scopes", () => {
  assert.throws(
    () => validateJobParameters({ from: "2026-08-13", to: "2026-08-01", stations: ["KNYC"] }),
    /on or after/,
  );
  assert.throws(
    () => validateJobParameters({ from: "2024-01-01", to: "2026-01-01", stations: ["KNYC"] }),
    /at most 730 days/,
  );
  assert.throws(
    () => validateJobParameters({ from: "2026-08-01", to: "2026-08-02", stations: ["KNYC", "KNYC"] }),
    /invalid or duplicated/,
  );
  assert.throws(
    () => validateJobParameters({ from: "2026-08-01", to: "2026-08-02", stations: ["NOPE"] }),
    /configured station/,
  );
});
