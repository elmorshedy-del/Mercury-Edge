import { STATIONS } from "../config";
import { executeBacktest } from "../backtest/run";
import { backfillDay } from "./backfill";
import {
  claimNextItem,
  completeItem,
  failItem,
  finishJob,
  heartbeatJob,
  initializeBackfillItems,
  jobItemCounts,
  jobStatus,
} from "./repository";
import type { ResearchJob } from "./types";

function message(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

async function honorControl(jobId: string) {
  const status = await jobStatus(jobId);
  if (status === "pause_requested") {
    await finishJob(jobId, "paused", { reason: "Paused after the current atomic work item" });
    return false;
  }
  if (status === "cancel_requested") {
    await finishJob(jobId, "cancelled", { reason: "Cancelled after the current atomic work item" });
    return false;
  }
  return status === "running";
}

async function runBackfillJob(job: ResearchJob) {
  await initializeBackfillItems(job);
  const initialized = await jobItemCounts(job.id);
  await heartbeatJob(job.id, initialized.succeeded + initialized.failed, initialized.total);

  while (await honorControl(job.id)) {
    const item = await claimNextItem(job.id);
    if (!item) break;
    const station = STATIONS.find((candidate) => candidate.station === item.station);
    if (!station) {
      await failItem(job.id, item.id, 3, `Station ${item.station} is not configured`);
      continue;
    }
    let heartbeatInFlight = false;
    const heartbeat = setInterval(() => {
      if (heartbeatInFlight) return;
      heartbeatInFlight = true;
      void heartbeatJob(job.id).finally(() => { heartbeatInFlight = false; });
    }, 30_000);
    try {
      const result = await backfillDay(station, item.date, new Date());
      await completeItem(job.id, item.id, result);
    } catch (error) {
      await failItem(job.id, item.id, item.attempts, message(error));
    } finally {
      clearInterval(heartbeat);
    }
    const counts = await jobItemCounts(job.id);
    await heartbeatJob(job.id, counts.succeeded + counts.failed, counts.total);
  }

  const status = await jobStatus(job.id);
  if (status !== "running") return;
  const counts = await jobItemCounts(job.id);
  const result = {
    ...counts,
    coveragePercent: counts.total ? (counts.succeeded / counts.total) * 100 : 0,
  };
  await finishJob(
    job.id,
    counts.failed ? "succeeded_with_warnings" : "succeeded",
    result,
    counts.failed ? `${counts.failed} city-day items failed; inspect the audit log` : null,
  );
}

async function runBacktestJob(job: ResearchJob) {
  try {
    const result = await executeBacktest(job.parameters.from, job.parameters.to, {
      stations: job.parameters.stations,
      jobId: job.id,
      onProgress: async ({ current, total, signals }) => {
        await heartbeatJob(job.id, current, total);
        if (!(await honorControl(job.id))) throw new Error("JOB_CONTROLLED_STOP");
        if (signals < 0) throw new Error("Invalid signal count");
      },
      shouldContinue: async () => (await jobStatus(job.id)) === "running",
    });
    if ((await jobStatus(job.id)) === "running") {
      await finishJob(job.id, "succeeded", result);
    }
  } catch (error) {
    const current = await jobStatus(job.id);
    if (current === "pause_requested") {
      await finishJob(job.id, "paused", { reason: "Paused between event evaluations" });
      return;
    }
    if (current === "cancel_requested") {
      await finishJob(job.id, "cancelled", { reason: "Cancelled between event evaluations" });
      return;
    }
    if (current === "paused" || current === "cancelled") return;
    await finishJob(job.id, "failed", {}, message(error));
  }
}

export async function processJob(job: ResearchJob) {
  if (job.jobType === "backfill") return runBackfillJob(job);
  return runBacktestJob(job);
}
