CREATE TABLE IF NOT EXISTS research_jobs (
  id text PRIMARY KEY,
  job_type text NOT NULL CHECK (job_type IN ('backfill', 'backtest')),
  status text NOT NULL CHECK (status IN (
    'blocked', 'queued', 'running', 'pause_requested', 'paused',
    'cancel_requested', 'cancelled', 'succeeded',
    'succeeded_with_warnings', 'failed'
  )),
  parameters jsonb NOT NULL,
  progress_current integer NOT NULL DEFAULT 0,
  progress_total integer NOT NULL DEFAULT 0,
  cursor jsonb NOT NULL DEFAULT '{}'::jsonb,
  result jsonb NOT NULL DEFAULT '{}'::jsonb,
  error_message text,
  parent_job_id text REFERENCES research_jobs(id),
  requested_by text NOT NULL DEFAULT 'dashboard',
  created_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  heartbeat_at timestamptz,
  finished_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS research_jobs_queue_idx
  ON research_jobs(status, created_at);

CREATE TABLE IF NOT EXISTS research_job_items (
  id bigserial PRIMARY KEY,
  job_id text NOT NULL REFERENCES research_jobs(id) ON DELETE CASCADE,
  item_key text NOT NULL,
  station_code text REFERENCES stations(station_code),
  trade_date date,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN (
    'pending', 'running', 'succeeded', 'failed', 'skipped'
  )),
  attempts integer NOT NULL DEFAULT 0,
  observations_written integer NOT NULL DEFAULT 0,
  products_written integer NOT NULL DEFAULT 0,
  contracts_written integer NOT NULL DEFAULT 0,
  quotes_written integer NOT NULL DEFAULT 0,
  result jsonb NOT NULL DEFAULT '{}'::jsonb,
  error_message text,
  started_at timestamptz,
  finished_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (job_id, item_key)
);

CREATE INDEX IF NOT EXISTS research_job_items_work_idx
  ON research_job_items(job_id, status, id);

CREATE TABLE IF NOT EXISTS research_job_events (
  id bigserial PRIMARY KEY,
  job_id text NOT NULL REFERENCES research_jobs(id) ON DELETE CASCADE,
  item_id bigint REFERENCES research_job_items(id) ON DELETE SET NULL,
  event_type text NOT NULL,
  level text NOT NULL DEFAULT 'info' CHECK (level IN ('info', 'warning', 'error')),
  message text NOT NULL,
  data jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS research_job_events_job_idx
  ON research_job_events(job_id, created_at DESC);

ALTER TABLE backtest_runs
  ADD COLUMN IF NOT EXISTS job_id text REFERENCES research_jobs(id);

CREATE INDEX IF NOT EXISTS backtest_runs_job_idx
  ON backtest_runs(job_id);
