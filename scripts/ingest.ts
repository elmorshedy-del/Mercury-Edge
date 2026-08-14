import { STATIONS } from "../lib/config";
import { hasDatabase, pool } from "../lib/db";
import { ingestAll } from "../lib/ingest";

function stationSelection() {
  const value = process.argv.find((arg) => arg.startsWith("--stations="))?.split("=")[1];
  if (!value) return STATIONS;
  const wanted = new Set(value.toUpperCase().split(","));
  return STATIONS.filter((station) => wanted.has(station.station));
}

async function main() {
  if (!hasDatabase) throw new Error("DATABASE_URL is required for ingestion");
  const results = await ingestAll(new Date(), stationSelection());
  console.log(JSON.stringify(results, null, 2));
  await pool?.end();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
