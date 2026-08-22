CREATE TABLE IF NOT EXISTS paper_experiment_queue (
  signal_id bigint PRIMARY KEY REFERENCES paper_signals(id) ON DELETE CASCADE,
  session_id text NOT NULL REFERENCES paper_sessions(id) ON DELETE CASCADE,
  strategy_code text NOT NULL,
  bundle jsonb NOT NULL,
  strategy_config jsonb NOT NULL,
  fee_multiplier numeric(12,6) NOT NULL,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed')),
  attempts integer NOT NULL DEFAULT 0,
  enqueued_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  finished_at timestamptz,
  error_message text
);
CREATE INDEX IF NOT EXISTS paper_experiment_queue_work_idx
  ON paper_experiment_queue(status,enqueued_at);
