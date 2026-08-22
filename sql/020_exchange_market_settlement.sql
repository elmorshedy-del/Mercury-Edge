-- Step 4H-D prerequisite: an exchange can authoritatively resolve contract
-- outcomes without Mercury proving that any returned market field is the
-- physical final temperature. Preserve exchange market results in their own
-- immutable ledger instead of weakening the numeric SettlementTruth contract.

CREATE TABLE IF NOT EXISTS exchange_market_settlements (
  exchange_settlement_id text PRIMARY KEY,
  session_id text NOT NULL REFERENCES paper_sessions(id) ON DELETE RESTRICT,
  event_ticker text NOT NULL,
  station_code text NOT NULL,
  climate_date date NOT NULL,
  raw_source_id bigint NOT NULL REFERENCES raw_source_journal(id) ON DELETE RESTRICT,
  rules_hash text NOT NULL CHECK (rules_hash ~ '^[0-9a-f]{64}$'),
  rule_source_name text NOT NULL,
  captured_at timestamptz NOT NULL,
  market_results jsonb NOT NULL CHECK (jsonb_typeof(market_results) = 'array'),
  parser_version text NOT NULL,
  settlement_payload jsonb NOT NULL,
  settlement_sha256 text NOT NULL CHECK (settlement_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (jsonb_array_length(market_results) > 0)
);
CREATE INDEX IF NOT EXISTS exchange_market_settlements_event_idx
  ON exchange_market_settlements(session_id,event_ticker,climate_date,captured_at,exchange_settlement_id);
CREATE INDEX IF NOT EXISTS exchange_market_settlements_raw_idx
  ON exchange_market_settlements(raw_source_id,exchange_settlement_id);

DROP TRIGGER IF EXISTS exchange_market_settlements_immutable ON exchange_market_settlements;
CREATE TRIGGER exchange_market_settlements_immutable
BEFORE UPDATE OR DELETE ON exchange_market_settlements
FOR EACH ROW EXECUTE FUNCTION mercury_reject_immutable_mutation();

ALTER TABLE settlement_audit_results
  ADD COLUMN IF NOT EXISTS exchange_settlement_id text
    REFERENCES exchange_market_settlements(exchange_settlement_id) ON DELETE RESTRICT;

ALTER TABLE settlement_audit_results
  DROP CONSTRAINT IF EXISTS settlement_audit_results_check;
ALTER TABLE settlement_audit_results
  DROP CONSTRAINT IF EXISTS settlement_audit_results_source_check;
ALTER TABLE settlement_audit_results
  ADD CONSTRAINT settlement_audit_results_source_check CHECK (
    settlement_id IS NOT NULL
    OR validation_id IS NOT NULL
    OR exchange_settlement_id IS NOT NULL
  );

CREATE INDEX IF NOT EXISTS settlement_audit_results_exchange_idx
  ON settlement_audit_results(exchange_settlement_id,audit_id)
  WHERE exchange_settlement_id IS NOT NULL;
