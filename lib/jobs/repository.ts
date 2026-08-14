import { randomUUID } from "node:crypto";
import type { PoolClient } from "pg";
import { STATIONS } from "../config";
import { pool, transaction } from "../db";
import { eachUtcDate } from "../time";
import type {
  JobControlAction,
  ResearchJob,
  ResearchJobParameters,
  ResearchJobStatus,
  ResearchJobType,
} from "./types";

type JobRow = {
  id: string;
  job_type: ResearchJobType;
  status: ResearchJobStatus;
  parameters: ResearchJobParameters;
  progress_current: number;
  progress_total: number;
  result: Record<string, unknown>;
  error_message: string | null;
  parent_job_id: string | null;
  created_at: Date;
  started_at: Date | null;
  heartbeat_at: Date | null;
  finished_at: Date | null;
  updated_at: Date;
};

export function normalizeJob(row: JobRow): ResearchJob {
  return {
    id: row.id,
    jobType: row.job_type,
    status: row.status,
    parameters: row.parameters,
    progressCurrent: Number(row.progress_current),
    progressTotal: Number(row.progress_total),
    result: row.result ?? {},
    errorMessage: row.error_message,
    parentJobId: row.parent_job_id,
    createdAt: row.created_at.toISOString(),
    startedAt: row.started_at?.toISOString() ?? null,
    heartbeatAt: row.heartbeat_at?.toISOString() ?? null,
    finishedAt: row.finished_at?.toISOString() ?? null,
    updatedAt: row.updated_at.toISOString(),
  };
}

async function insertEvent(
  client: PoolClient,
  jobId: string,
  eventType: string,
  message: string,
  level: "info" | "warning" | "error" = "info",
  data: Record<string, unknown> = {},
  itemId: number | null = null,
) {
  await client.query(
    `INSERT INTO research_job_events
      (job_id, item_id, event_type, level, message, data)
     VALUES ($1,$2,$3,$4,$5,$6)`,
    [jobId, itemId, eventType, level, message, data],
  );
}

async function insertJob(
  client: PoolClient,
  jobType: ResearchJobType,
  parameters: ResearchJobParameters,
  status: ResearchJobStatus,
  parentJobId: string | null,
) {
  const id = `job_${randomUUID()}`;
  await client.query(
    `INSERT INTO research_jobs
      (id, job_type, status, parameters, parent_job_id)
     VALUES ($1,$2,$3,$4,$5)`,
    [id, jobType, status, parameters, parentJobId],
  );
  await insertEvent(
    client,
    id,
    "job_created",
    `${jobType} job ${status === "blocked" ? "created behind its backfill dependency" : "queued"}`,
    "info",
    { parameters, parentJobId },
  );
  return id;
}

export async function createJob(
  jobType: ResearchJobType,
  parameters: ResearchJobParameters,
) {
  return transaction((client) => insertJob(client, jobType, parameters, "queued", null));
}

export async function createPipeline(parameters: ResearchJobParameters) {
  return transaction(async (client) => {
    const backfillJobId = await insertJob(client, "backfill", parameters, "queued", null);
    const backtestJobId = await insertJob(client, "backtest", parameters, "blocked", backfillJobId);
    return { backfillJobId, backtestJobId };
  });
}

export async function listJobs(limit = 30) {
  if (!pool) return [];
  const result = await pool.query<JobRow>(
    `SELECT * FROM research_jobs ORDER BY created_at DESC LIMIT $1`,
    [Math.max(1, Math.min(limit, 100))],
  );
  return result.rows.map(normalizeJob);
}

export async function getJobDetail(id: string) {
  if (!pool) return null;
  const jobResult = await pool.query<JobRow>(`SELECT * FROM research_jobs WHERE id=$1`, [id]);
  if (!jobResult.rowCount) return null;
  const [items, events] = await Promise.all([
    pool.query<{
      id: string; item_key: string; station_code: string | null; trade_date: string | null;
      status: string; attempts: number; observations_written: number; contracts_written: number;
      quotes_written: number; result: Record<string, unknown>; error_message: string | null;
      started_at: Date | null; finished_at: Date | null;
    }>(
      `SELECT id, item_key, station_code, trade_date::text, status, attempts,
              observations_written, contracts_written, quotes_written, result,
              error_message, started_at, finished_at
         FROM research_job_items WHERE job_id=$1 ORDER BY id LIMIT 1000`,
      [id],
    ),
    pool.query<{
      id: string; item_id: string | null; event_type: string; level: string;
      message: string; data: Record<string, unknown>; created_at: Date;
    }>(
      `SELECT id, item_id, event_type, level, message, data, created_at
         FROM research_job_events WHERE job_id=$1 ORDER BY created_at DESC LIMIT 250`,
      [id],
    ),
  ]);
  return {
    job: normalizeJob(jobResult.rows[0]),
    items: items.rows.map((item) => ({
      id: Number(item.id),
      itemKey: item.item_key,
      station: item.station_code,
      date: item.trade_date?.slice(0, 10) ?? null,
      status: item.status,
      attempts: item.attempts,
      observations: item.observations_written,
      contracts: item.contracts_written,
      quotes: item.quotes_written,
      result: item.result,
      errorMessage: item.error_message,
      startedAt: item.started_at?.toISOString() ?? null,
      finishedAt: item.finished_at?.toISOString() ?? null,
    })),
    events: events.rows.map((event) => ({
      id: Number(event.id),
      itemId: event.item_id === null ? null : Number(event.item_id),
      eventType: event.event_type,
      level: event.level,
      message: event.message,
      data: event.data,
      createdAt: event.created_at.toISOString(),
    })),
  };
}

export async function requestJobAction(id: string, action: JobControlAction) {
  if (!pool) throw new Error("DATABASE_URL is not configured");
  return transaction(async (client) => {
    const result = await client.query<JobRow>(
      `SELECT * FROM research_jobs WHERE id=$1 FOR UPDATE`,
      [id],
    );
    if (!result.rowCount) return null;
    const job = result.rows[0];
    let next: ResearchJobStatus | null = null;
    if (action === "pause") {
      if (job.status === "running") next = "pause_requested";
      if (job.status === "queued") next = "paused";
    } else if (action === "resume" && job.status === "paused") {
      next = "queued";
    } else if (action === "cancel") {
      if (job.status === "running" || job.status === "pause_requested") next = "cancel_requested";
      if (["queued", "paused", "blocked"].includes(job.status)) next = "cancelled";
    } else if (action === "retry" && ["failed", "succeeded_with_warnings", "cancelled"].includes(job.status)) {
      next = job.parent_job_id ? "blocked" : "queued";
      await client.query(
        `UPDATE research_job_items SET status='pending', error_message=NULL,
                started_at=NULL, finished_at=NULL, updated_at=now()
          WHERE job_id=$1 AND status IN ('failed','running')`,
        [id],
      );
    }
    if (!next) throw new Error(`Action ${action} is not valid while job is ${job.status}`);
    await client.query(
      `UPDATE research_jobs SET status=$2, error_message=NULL,
              finished_at=CASE WHEN $2 IN ('cancelled') THEN now() ELSE NULL END,
              updated_at=now() WHERE id=$1`,
      [id, next],
    );
    await insertEvent(client, id, `job_${action}`, `Job ${action} requested`, "info", { from: job.status, to: next });
    if (next === "cancelled") {
      const children = await client.query<{ id: string }>(
        `UPDATE research_jobs SET status='cancelled', finished_at=now(), updated_at=now()
          WHERE parent_job_id=$1 AND status='blocked' RETURNING id`,
        [id],
      );
      for (const child of children.rows) {
        await insertEvent(
          client,
          child.id,
          "dependency_cancelled",
          "Cancelled because the required parent job was cancelled",
          "warning",
          { parentJobId: id },
        );
      }
    }
    return next;
  });
}

export async function initializeBackfillItems(job: ResearchJob) {
  if (!pool) throw new Error("DATABASE_URL is not configured");
  await transaction(async (client) => {
    for (const stationCode of job.parameters.stations) {
      const station = STATIONS.find((item) => item.station === stationCode);
      if (!station) continue;
      await client.query(
        `INSERT INTO stations
          (station_code, city, timezone, kalshi_series, nws_location, iem_network)
         VALUES ($1,$2,$3,$4,$5,$6)
         ON CONFLICT (station_code) DO NOTHING`,
        [station.station, station.city, station.timezone, station.kalshiSeries,
         station.nwsLocation, station.iemNetwork],
      );
      for (const date of eachUtcDate(job.parameters.from, job.parameters.to)) {
        await client.query(
          `INSERT INTO research_job_items
            (job_id, item_key, station_code, trade_date)
           VALUES ($1,$2,$3,$4::date) ON CONFLICT (job_id, item_key) DO NOTHING`,
          [job.id, `${stationCode}:${date}`, stationCode, date],
        );
      }
    }
    const count = await client.query<{ count: string }>(
      `SELECT count(*)::text AS count FROM research_job_items WHERE job_id=$1`,
      [job.id],
    );
    await client.query(
      `UPDATE research_jobs SET progress_total=$2, updated_at=now() WHERE id=$1`,
      [job.id, Number(count.rows[0].count)],
    );
  });
}

export async function claimNextJob() {
  if (!pool) return null;
  return transaction(async (client) => {
    await client.query(
      `UPDATE research_jobs child SET status='queued', updated_at=now()
        FROM research_jobs parent
       WHERE child.status='blocked' AND child.parent_job_id=parent.id
         AND parent.status='succeeded'`,
    );
    const result = await client.query<JobRow>(
      `SELECT * FROM research_jobs
        WHERE status='queued'
        ORDER BY created_at
        FOR UPDATE SKIP LOCKED LIMIT 1`,
    );
    if (!result.rowCount) return null;
    const row = result.rows[0];
    await client.query(
      `UPDATE research_jobs SET status='running',
              started_at=COALESCE(started_at, now()), heartbeat_at=now(),
              updated_at=now() WHERE id=$1`,
      [row.id],
    );
    await insertEvent(client, row.id, "job_started", `${row.job_type} worker started or resumed`);
    return normalizeJob({ ...row, status: "running", started_at: row.started_at ?? new Date(), heartbeat_at: new Date() });
  });
}

export async function recoverStaleJobs() {
  if (!pool) return;
  await transaction(async (client) => {
    const stale = await client.query<{ id: string; status: ResearchJobStatus }>(
      `SELECT id, status FROM research_jobs
        WHERE status IN ('running','pause_requested','cancel_requested')
          AND COALESCE(heartbeat_at, started_at, created_at) < now() - interval '10 minutes'
        FOR UPDATE`,
    );
    for (const row of stale.rows) {
      const recoveredStatus: ResearchJobStatus = row.status === "pause_requested"
        ? "paused"
        : row.status === "cancel_requested"
          ? "cancelled"
          : "queued";
      await client.query(
        `UPDATE research_jobs SET status=$2,
                finished_at=CASE WHEN $2='cancelled' THEN now() ELSE finished_at END,
                updated_at=now() WHERE id=$1`,
        [row.id, recoveredStatus],
      );
      await client.query(
        `UPDATE research_job_items SET status='pending', updated_at=now()
          WHERE job_id=$1 AND status='running'`,
        [row.id],
      );
      await insertEvent(
        client,
        row.id,
        "job_recovered",
        recoveredStatus === "queued"
          ? "Stale running job returned to the queue"
          : `Stale ${row.status.replaceAll("_", " ")} finalized as ${recoveredStatus}`,
        "warning",
        { from: row.status, to: recoveredStatus },
      );
    }
  });
}

export async function jobStatus(id: string) {
  if (!pool) return null;
  const result = await pool.query<{ status: ResearchJobStatus }>(
    `SELECT status FROM research_jobs WHERE id=$1`,
    [id],
  );
  return result.rows[0]?.status ?? null;
}

export async function heartbeatJob(id: string, current?: number, total?: number) {
  if (!pool) return;
  await pool.query(
    `UPDATE research_jobs SET heartbeat_at=now(),
            progress_current=COALESCE($2, progress_current),
            progress_total=COALESCE($3, progress_total), updated_at=now()
      WHERE id=$1`,
    [id, current ?? null, total ?? null],
  );
}

export async function claimNextItem(jobId: string) {
  if (!pool) return null;
  return transaction(async (client) => {
    const result = await client.query<{
      id: string; station_code: string; trade_date: string; attempts: number;
    }>(
      `SELECT id, station_code, trade_date::text, attempts
         FROM research_job_items
        WHERE job_id=$1 AND status='pending'
        ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1`,
      [jobId],
    );
    if (!result.rowCount) return null;
    const item = result.rows[0];
    await client.query(
      `UPDATE research_job_items SET status='running', attempts=attempts+1,
              started_at=COALESCE(started_at, now()), updated_at=now() WHERE id=$1`,
      [item.id],
    );
    return { id: Number(item.id), station: item.station_code, date: item.trade_date.slice(0, 10), attempts: item.attempts + 1 };
  });
}

export async function completeItem(
  jobId: string,
  itemId: number,
  result: { observations: number; contracts: number; quotes: number; [key: string]: unknown },
) {
  if (!pool) return;
  await transaction(async (client) => {
    await client.query(
      `UPDATE research_job_items SET status='succeeded',
              observations_written=$2, contracts_written=$3, quotes_written=$4,
              result=$5, error_message=NULL, finished_at=now(), updated_at=now()
        WHERE id=$1`,
      [itemId, result.observations, result.contracts, result.quotes, result],
    );
    await insertEvent(client, jobId, "item_succeeded", `${result.station} ${result.date} stored`, "info", result, itemId);
  });
}

export async function failItem(jobId: string, itemId: number, attempts: number, message: string) {
  if (!pool) return;
  const permanent = /\b(400|401|403|404)\b/.test(message) || attempts >= 3;
  await transaction(async (client) => {
    await client.query(
      `UPDATE research_job_items SET status=$2, error_message=$3,
              finished_at=CASE WHEN $2='failed' THEN now() ELSE NULL END,
              updated_at=now() WHERE id=$1`,
      [itemId, permanent ? "failed" : "pending", message],
    );
    await insertEvent(
      client,
      jobId,
      permanent ? "item_failed" : "item_retry_scheduled",
      permanent ? message : `Retry ${attempts}/3 scheduled: ${message}`,
      permanent ? "error" : "warning",
      { attempts },
      itemId,
    );
  });
}

export async function jobItemCounts(jobId: string) {
  if (!pool) return { total: 0, succeeded: 0, failed: 0, pending: 0 };
  const result = await pool.query<{ status: string; count: string }>(
    `SELECT status, count(*)::text AS count FROM research_job_items
      WHERE job_id=$1 GROUP BY status`,
    [jobId],
  );
  const counts = Object.fromEntries(result.rows.map((row) => [row.status, Number(row.count)]));
  return {
    total: Object.values(counts).reduce((sum, value) => sum + value, 0),
    succeeded: counts.succeeded ?? 0,
    failed: counts.failed ?? 0,
    pending: (counts.pending ?? 0) + (counts.running ?? 0),
  };
}

export async function finishJob(
  id: string,
  status: Extract<ResearchJobStatus, "succeeded" | "succeeded_with_warnings" | "failed" | "paused" | "cancelled">,
  result: Record<string, unknown> = {},
  errorMessage: string | null = null,
) {
  if (!pool) return;
  await transaction(async (client) => {
    await client.query(
      `UPDATE research_jobs SET status=$2, result=$3, error_message=$4,
              finished_at=CASE WHEN $2 IN ('paused') THEN NULL ELSE now() END,
              heartbeat_at=now(), updated_at=now() WHERE id=$1`,
      [id, status, result, errorMessage],
    );
    await insertEvent(
      client,
      id,
      `job_${status}`,
      errorMessage ?? `Job ${status.replaceAll("_", " ")}`,
      status === "failed" ? "error" : status === "succeeded_with_warnings" ? "warning" : "info",
      result,
    );
    if (status === "failed" || status === "cancelled") {
      const children = await client.query<{ id: string }>(
        `UPDATE research_jobs SET status='cancelled', finished_at=now(), updated_at=now()
          WHERE parent_job_id=$1 AND status='blocked' RETURNING id`,
        [id],
      );
      for (const child of children.rows) {
        await insertEvent(
          client,
          child.id,
          "dependency_unavailable",
          `Cancelled because the required parent job ${status}`,
          "warning",
          { parentJobId: id, parentStatus: status },
        );
      }
    }
  });
}
