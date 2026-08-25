-- Step 4D: append-only canonical hard-state timeline.
--
-- Evidence derivations are immutable facts. This layer records how a specific
-- accumulator/calendar version applied each fact and which applications created
-- monotonic state transitions. Later corroboration is appended; it never
-- rewrites the first-known transition.

CREATE TABLE IF NOT EXISTS hard_state_applications (
  application_id text PRIMARY KEY,
  session_id text NOT NULL REFERENCES paper_sessions(id) ON DELETE RESTRICT,
  station_code text NOT NULL,
  climate_date date NOT NULL,
  evidence_id text NOT NULL REFERENCES evidence_derivations(evidence_id) ON DELETE RESTRICT,
  status text NOT NULL CHECK (status IN ('transition','corroboration','rejected','duplicate')),
  reason text NOT NULL,
  known_at timestamptz NOT NULL,
  proven_min_f integer,
  prior_bound_f integer,
  resulting_bound_f integer,
  evidence_type text NOT NULL,
  accumulator_version text NOT NULL,
  calendar_version text NOT NULL,
  application_payload jsonb NOT NULL,
  application_sha256 text NOT NULL CHECK (application_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(session_id,station_code,climate_date,accumulator_version,evidence_id)
);
CREATE INDEX IF NOT EXISTS hard_state_applications_day_idx
  ON hard_state_applications(session_id,station_code,climate_date,known_at,application_id);
CREATE INDEX IF NOT EXISTS hard_state_applications_status_idx
  ON hard_state_applications(status,reason,known_at);

CREATE TABLE IF NOT EXISTS hard_state_transitions (
  state_id text PRIMARY KEY,
  session_id text NOT NULL REFERENCES paper_sessions(id) ON DELETE RESTRICT,
  station_code text NOT NULL,
  climate_date date NOT NULL,
  proven_daily_high_min_f integer NOT NULL,
  first_known_at timestamptz NOT NULL,
  transition_evidence_id text NOT NULL REFERENCES evidence_derivations(evidence_id) ON DELETE RESTRICT,
  supporting_evidence_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
  state_model_version text NOT NULL,
  calendar_version text NOT NULL,
  transition_payload jsonb NOT NULL,
  transition_sha256 text NOT NULL CHECK (transition_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(
    session_id,station_code,climate_date,state_model_version,
    proven_daily_high_min_f,transition_evidence_id
  )
);
CREATE INDEX IF NOT EXISTS hard_state_transitions_day_idx
  ON hard_state_transitions(session_id,station_code,climate_date,first_known_at,state_id);
CREATE INDEX IF NOT EXISTS hard_state_transitions_version_idx
  ON hard_state_transitions(state_model_version,calendar_version);

-- The application/transition record is an audit ledger, not mutable current
-- state. If interpretation logic changes, write a new versioned derivation and
-- accumulator output instead of changing old rows.
DROP TRIGGER IF EXISTS hard_state_applications_immutable ON hard_state_applications;
CREATE TRIGGER hard_state_applications_immutable
BEFORE UPDATE OR DELETE ON hard_state_applications
FOR EACH ROW EXECUTE FUNCTION mercury_reject_immutable_mutation();

DROP TRIGGER IF EXISTS hard_state_transitions_immutable ON hard_state_transitions;
CREATE TRIGGER hard_state_transitions_immutable
BEFORE UPDATE OR DELETE ON hard_state_transitions
FOR EACH ROW EXECUTE FUNCTION mercury_reject_immutable_mutation();
