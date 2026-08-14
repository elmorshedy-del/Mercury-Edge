import { STATIONS } from "../lib/config";
import { pool } from "../lib/db";
import { backfillDay } from "../lib/jobs/backfill";
import { eachUtcDate } from "../lib/time";

function value(name: string) {
  return process.argv.find((arg) => arg.startsWith(`--${name}=`))?.split("=")[1];
}

function selection() {
  const requested = value("stations");
  if (!requested) return STATIONS;
  const wanted = new Set(requested.toUpperCase().split(","));
  return STATIONS.filter((station) => wanted.has(station.station));
}

async function main() {
  if (!pool) throw new Error("DATABASE_URL is required for backfill");
  const from = value("from");
  const to = value("to") ?? from;
  if (!from || !to) {
    throw new Error("Usage: npm run backfill -- --from=YYYY-MM-DD --to=YYYY-MM-DD --stations=KNYC,KPHL");
  }
  const results = [];
  for (const date of eachUtcDate(from, to)) {
    for (const station of selection()) {
      try {
        const result = await backfillDay(station, date);
        results.push(result);
        console.log(JSON.stringify(result));
      } catch (error) {
        console.error(JSON.stringify({ station: station.station, date, error: error instanceof Error ? error.message : String(error) }));
      }
    }
  }
  console.log(JSON.stringify({ completed: results.length }, null, 2));
  await pool.end();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
