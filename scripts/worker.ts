import { STATIONS } from "../lib/config";
import { ingestAll } from "../lib/ingest";

const intervalMs = Math.max(30_000, Number(process.env.POLL_INTERVAL_MS ?? 60_000));
const stationFilter = new Set(
  (process.env.INGEST_STATIONS ?? "KNYC,KPHL")
    .split(",")
    .map((station) => station.trim().toUpperCase())
    .filter(Boolean),
);
const activeStations = STATIONS.filter((station) => stationFilter.has(station.station));

async function cycle() {
  const started = new Date();
  const results = await ingestAll(started, activeStations);
  const records = results.reduce(
    (sum, result) => sum + result.observations + result.products + result.quotes,
    0,
  );
  const errors = results.flatMap((result) => result.errors);
  console.log(JSON.stringify({ at: started.toISOString(), records, errors }));
}

async function main() {
  if (process.env.LIVE_INGEST_ENABLED !== "1") {
    throw new Error("Set LIVE_INGEST_ENABLED=1 before starting the polling worker");
  }
  if (!activeStations.length) {
    throw new Error("INGEST_STATIONS does not contain a configured station");
  }
  while (true) {
    const started = Date.now();
    try {
      await cycle();
    } catch (error) {
      console.error(error);
    }
    const remaining = Math.max(1_000, intervalMs - (Date.now() - started));
    await new Promise((resolve) => setTimeout(resolve, remaining));
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
