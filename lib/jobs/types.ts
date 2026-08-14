export type ResearchJobType = "backfill" | "backtest";

export type ResearchJobStatus =
  | "blocked"
  | "queued"
  | "running"
  | "pause_requested"
  | "paused"
  | "cancel_requested"
  | "cancelled"
  | "succeeded"
  | "succeeded_with_warnings"
  | "failed";

export type ResearchJobParameters = {
  from: string;
  to: string;
  stations: string[];
};

export type ResearchJob = {
  id: string;
  jobType: ResearchJobType;
  status: ResearchJobStatus;
  parameters: ResearchJobParameters;
  progressCurrent: number;
  progressTotal: number;
  result: Record<string, unknown>;
  errorMessage: string | null;
  parentJobId: string | null;
  createdAt: string;
  startedAt: string | null;
  heartbeatAt: string | null;
  finishedAt: string | null;
  updatedAt: string;
};

export type JobControlAction = "pause" | "resume" | "cancel" | "retry";

export class JobControlSignal extends Error {
  constructor(public readonly signal: "pause" | "cancel") {
    super(`Job control signal: ${signal}`);
  }
}
