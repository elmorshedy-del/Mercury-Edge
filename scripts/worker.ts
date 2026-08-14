import { STATIONS } from "../lib/config";
import { ingestAll } from "../lib/ingest";

const intervalMs = Math.max(30_000, Number(process.env.POLL_INTERVAL_MS ?? 60_000));

async function cycle() {
  const started = new Date();
  const results = await ingestAll(started, STATIONS);
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
