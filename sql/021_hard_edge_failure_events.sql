-- Step 4I-B: append-only structured hard-edge failure/non-admission ledger.
--
-- The ledger makes fail-closed behavior countable and replayable. It is not a
-- mutable alert table: retries are idempotent, changed interpretations require a
-- changed model/reason/identity, and UPDATE/DELETE are forbidden.

CREATE TABLE IF NOT EXISTS hard_edge_failure_events (
  failure_id text PRIMARY KEY,
  session_id text NOT NULL REFERENCES paper_sessions(id) ON DELETE RESTRICT,
  stage text NOT NULL CHECK (
    stage IN (
      'source_parse','evidence','hard_state','elimination','execution',
      'validation','settlement','replay'
    )
  ),
  disposition_class text NOT NULL CHECK (
    disposition_class IN (
      'integrity_failure','fail_closed','non_admission','economic_skip',
      'invariant_failure'
    )
  ),
  reason_code text NOT NULL CHECK (reason_code ~ '^[A-Z0-9_]+$'),
  station_code text,
  climate_date date,
  event_ticker text,
  market_ticker text,
  raw_source_id bigint REFERENCES raw_source_journal(id) ON DELETE RESTRICT,
  evidence_id text REFERENCES evidence_derivations(evidence_id) ON DELETE RESTRICT,
  state_id text REFERENCES hard_state_transitions(state_id) ON DELETE RESTRICT,
  elimination_id text,
  signal_id bigint REFERENCES paper_signals(id) ON DELETE RESTRICT,
  order_id bigint REFERENCES paper_orders(id) ON DELETE RESTRICT,
  occurred_at timestamptz NOT NULL,
  failure_model_version text NOT NULL,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  failure_payload jsonb NOT NULL,
  failure_sha256 text NOT NULL CHECK (failure_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS hard_edge_failure_events_stage_reason_idx
  ON hard_edge_failure_events(session_id,stage,reason_code,occurred_at,failure_id);
CREATE INDEX IF NOT EXISTS hard_edge_failure_events_station_day_idx
  ON hard_edge_failure_events(station_code,climate_date,stage,occurred_at,failure_id)
  WHERE station_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS hard_edge_failure_events_raw_idx
  ON hard_edge_failure_events(raw_source_id,failure_id)
  WHERE raw_source_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS hard_edge_failure_events_order_idx
  ON hard_edge_failure_events(order_id,failure_id)
  WHERE order_id IS NOT NULL;

DROP TRIGGER IF EXISTS hard_edge_failure_events_immutable ON hard_edge_failure_events;
CREATE TRIGGER hard_edge_failure_events_immutable
BEFORE UPDATE OR DELETE ON hard_edge_failure_events
FOR EACH ROW EXECUTE FUNCTION mercury_reject_immutable_mutation();
