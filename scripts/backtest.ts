import { pool } from "../lib/db";
import { executeBacktest } from "../lib/backtest/run";

function value(name: string) {
  return process.argv.find((arg) => arg.startsWith(`--${name}=`))?.split("=")[1];
}

async function main() {
  const from = value("from");
  const to = value("to") ?? from;
  if (!from || !to) {
    throw new Error("Usage: npm run backtest -- --from=YYYY-MM-DD --to=YYYY-MM-DD");
  }
  const summary = await executeBacktest(from, to);
  console.log(JSON.stringify(summary, null, 2));
  await pool?.end();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
