import { pool } from "../lib/db";
import { claimNextJob, finishJob, jobStatus, recoverStaleJobs } from "../lib/jobs/repository";
import { processJob } from "../lib/jobs/worker";

const idleMs = Math.max(1_000, Number(process.env.JOB_POLL_INTERVAL_MS ?? 5_000));

async function main() {
  if (!pool) throw new Error("DATABASE_URL is required for the research job worker");
  await recoverStaleJobs();
  let lastRecovery = Date.now();
  while (true) {
    if (Date.now() - lastRecovery > 60_000) {
      await recoverStaleJobs();
      lastRecovery = Date.now();
    }
    const job = await claimNextJob();
    if (!job) {
      await new Promise((resolve) => setTimeout(resolve, idleMs));
      continue;
    }
    try {
      await processJob(job);
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      console.error(JSON.stringify({
        jobId: job.id,
        error: errorMessage,
      }));
      if ((await jobStatus(job.id)) === "running") {
        await finishJob(job.id, "failed", {}, `Unexpected worker failure: ${errorMessage}`);
      }
    }
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
