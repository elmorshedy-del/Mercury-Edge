-- Step 4H-B: immutable validation, settlement-truth and settlement-audit journal.
--
-- NWS DSM/CLI products are validation/corroboration records. Contract-authoritative
-- settlement truth is stored separately. Audit outputs are append-only derivations
-- over those immutable facts and the existing hard-state/order provenance.

CREATE TABLE IF NOT EXISTS validation_products (
  validation_id text PRIMARY KEY,
  session_id text NOT NULL REFERENCES paper_sessions(id) ON DELETE RESTRICT,
  source text NOT NULL,
  source_product_id text NOT NULL,
  station_code text NOT NULL,
  climate_date date,
  reported_max_f integer,
  max_observed_at timestamptz,
  issued_at timestamptz NOT NULL,
  mercury_received_at timestamptz NOT NULL,
  raw_source_id bigint NOT NULL REFERENCES raw_source_journal(id) ON DELETE RESTRICT,
  source_payload_sha256 text NOT NULL CHECK (source_payload_sha256 ~ '^[0-9a-f]{64}$'),
  lifecycle text NOT NULL CHECK (
    lifecycle IN (
      'current_day_preliminary','completed_day_preliminary',
      'authoritative_final','ambiguous','rejected'
    )
  ),
  authority text NOT NULL CHECK (
    authority IN ('corroboration_only','contract_authoritative','exchange_result')
  ),
  corrected boolean NOT NULL DEFAULT false,
  revision_of text REFERENCES validation_products(validation_id) ON DELETE RESTRICT,
  parser_version text NOT NULL,
  validation_model_version text NOT NULL,
  calendar_version text NOT NULL,
  fail_closed_reason text,
  product_payload jsonb NOT NULL,
  product_sha256 text NOT NULL CHECK (product_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS validation_products_station_day_idx
  ON validation_products(session_id,station_code,climate_date,issued_at,validation_id);
CREATE INDEX IF NOT EXISTS validation_products_source_product_idx
  ON validation_products(source,source_product_id,issued_at,validation_id);
CREATE INDEX IF NOT EXISTS validation_products_raw_idx
  ON validation_products(raw_source_id,validation_id);

CREATE TABLE IF NOT EXISTS authoritative_settlements (
  settlement_id text PRIMARY KEY,
  truth_id text NOT NULL UNIQUE,
  session_id text NOT NULL REFERENCES paper_sessions(id) ON DELETE RESTRICT,
  event_ticker text NOT NULL,
  station_code text NOT NULL,
  climate_date date NOT NULL,
  final_max_f integer NOT NULL,
  source text NOT NULL,
  raw_source_id bigint NOT NULL REFERENCES raw_source_journal(id) ON DELETE RESTRICT,
  rules_hash text NOT NULL CHECK (rules_hash ~ '^[0-9a-f]{64}$'),
  rule_source_name text NOT NULL,
  settlement_source_name text NOT NULL,
  authority text NOT NULL CHECK (authority IN ('contract_authoritative','exchange_result')),
  observed_or_issued_at timestamptz NOT NULL,
  revision_of_truth_id text REFERENCES authoritative_settlements(truth_id) ON DELETE RESTRICT,
  truth_model_version text NOT NULL,
  parser_version text NOT NULL,
  settlement_payload jsonb NOT NULL,
  settlement_sha256 text NOT NULL CHECK (settlement_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS authoritative_settlements_event_idx
  ON authoritative_settlements(session_id,event_ticker,climate_date,observed_or_issued_at,settlement_id);
CREATE INDEX IF NOT EXISTS authoritative_settlements_station_day_idx
  ON authoritative_settlements(station_code,climate_date,observed_or_issued_at,settlement_id);
CREATE INDEX IF NOT EXISTS authoritative_settlements_raw_idx
  ON authoritative_settlements(raw_source_id,settlement_id);

CREATE TABLE IF NOT EXISTS settlement_audit_results (
  audit_id text PRIMARY KEY,
  session_id text NOT NULL REFERENCES paper_sessions(id) ON DELETE RESTRICT,
  settlement_id text REFERENCES authoritative_settlements(settlement_id) ON DELETE RESTRICT,
  validation_id text REFERENCES validation_products(validation_id) ON DELETE RESTRICT,
  severity text NOT NULL CHECK (severity IN ('info','warning','critical')),
  status text NOT NULL CHECK (status IN ('pass','discrepancy','invariant_failure')),
  finding_code text NOT NULL,
  station_code text NOT NULL,
  climate_date date NOT NULL,
  state_id text REFERENCES hard_state_transitions(state_id) ON DELETE RESTRICT,
  elimination_id text,
  order_id bigint REFERENCES paper_orders(id) ON DELETE RESTRICT,
  market_ticker text,
  auditor_version text NOT NULL,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  audit_payload jsonb NOT NULL,
  audit_sha256 text NOT NULL CHECK (audit_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (settlement_id IS NOT NULL OR validation_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS settlement_audit_results_day_idx
  ON settlement_audit_results(session_id,station_code,climate_date,severity,created_at,audit_id);
CREATE INDEX IF NOT EXISTS settlement_audit_results_state_idx
  ON settlement_audit_results(state_id,audit_id) WHERE state_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS settlement_audit_results_order_idx
  ON settlement_audit_results(order_id,audit_id) WHERE order_id IS NOT NULL;

-- These are historical ledgers. A correction, revised truth or newer auditor
-- produces a new row. Nothing rewrites the original product/truth/audit result.
DROP TRIGGER IF EXISTS validation_products_immutable ON validation_products;
CREATE TRIGGER validation_products_immutable
BEFORE UPDATE OR DELETE ON validation_products
FOR EACH ROW EXECUTE FUNCTION mercury_reject_immutable_mutation();

DROP TRIGGER IF EXISTS authoritative_settlements_immutable ON authoritative_settlements;
CREATE TRIGGER authoritative_settlements_immutable
BEFORE UPDATE OR DELETE ON authoritative_settlements
FOR EACH ROW EXECUTE FUNCTION mercury_reject_immutable_mutation();

DROP TRIGGER IF EXISTS settlement_audit_results_immutable ON settlement_audit_results;
CREATE TRIGGER settlement_audit_results_immutable
BEFORE UPDATE OR DELETE ON settlement_audit_results
FOR EACH ROW EXECUTE FUNCTION mercury_reject_immutable_mutation();
