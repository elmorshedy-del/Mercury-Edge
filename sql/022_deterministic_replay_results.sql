-- Step 4J-D: deterministic replay output is a new immutable derivation over a
-- source session. It must never mutate or masquerade as the live benchmark.

CREATE TABLE IF NOT EXISTS deterministic_replay_results (
  replay_result_id text PRIMARY KEY,
  source_session_id text NOT NULL REFERENCES paper_sessions(id) ON DELETE RESTRICT,
  manifest_id text NOT NULL,
  replay_policy text NOT NULL CHECK (replay_policy IN ('benchmark','research')),
  station_code text NOT NULL,
  climate_date date NOT NULL,
  event_ticker text NOT NULL,
  source_input_sha256 text NOT NULL CHECK (source_input_sha256 ~ '^[0-9a-f]{64}$'),
  version_bundle jsonb NOT NULL,
  version_bundle_sha256 text NOT NULL CHECK (version_bundle_sha256 ~ '^[0-9a-f]{64}$'),
  execution_config jsonb NOT NULL,
  execution_config_sha256 text NOT NULL CHECK (execution_config_sha256 ~ '^[0-9a-f]{64}$'),
  hard_state_output_sha256 text NOT NULL CHECK (hard_state_output_sha256 ~ '^[0-9a-f]{64}$'),
  execution_output_sha256 text NOT NULL CHECK (execution_output_sha256 ~ '^[0-9a-f]{64}$'),
  settlement_grade jsonb NOT NULL,
  settlement_grade_sha256 text NOT NULL CHECK (settlement_grade_sha256 ~ '^[0-9a-f]{64}$'),
  replay_payload jsonb NOT NULL,
  replay_payload_sha256 text NOT NULL CHECK (replay_payload_sha256 ~ '^[0-9a-f]{64}$'),
  replay_model_version text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (
    source_session_id, manifest_id, replay_policy,
    execution_config_sha256, replay_model_version
  )
);

CREATE INDEX IF NOT EXISTS deterministic_replay_results_source_idx
  ON deterministic_replay_results(source_session_id,station_code,climate_date,event_ticker,created_at,replay_result_id);
CREATE INDEX IF NOT EXISTS deterministic_replay_results_manifest_idx
  ON deterministic_replay_results(manifest_id,replay_result_id);

DROP TRIGGER IF EXISTS deterministic_replay_results_immutable ON deterministic_replay_results;
CREATE TRIGGER deterministic_replay_results_immutable
BEFORE UPDATE OR DELETE ON deterministic_replay_results
FOR EACH ROW EXECUTE FUNCTION mercury_reject_immutable_mutation();
