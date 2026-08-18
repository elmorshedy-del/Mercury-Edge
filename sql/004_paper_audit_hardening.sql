ALTER TABLE market_data_journal
  ADD COLUMN IF NOT EXISTS connection_id uuid,
  ADD COLUMN IF NOT EXISTS prev_chain_hash text,
  ADD COLUMN IF NOT EXISTS chain_hash text,
  ADD COLUMN IF NOT EXISTS price_mode text,
  ADD COLUMN IF NOT EXISTS source_message_type text;

CREATE INDEX IF NOT EXISTS market_data_journal_connection_stream_idx
  ON market_data_journal(connection_id, sid, seq, id);

CREATE TABLE IF NOT EXISTS settlement_rule_snapshots (
  id bigserial PRIMARY KEY,
  session_id text REFERENCES paper_sessions(id) ON DELETE CASCADE,
  series_ticker text NOT NULL,
  event_ticker text,
  captured_at timestamptz NOT NULL,
  rules_hash text NOT NULL,
  settlement_sources jsonb NOT NULL DEFAULT '[]'::jsonb,
  fee_type text,
  fee_multiplier numeric(12,6),
  important_info jsonb,
  raw_payload jsonb NOT NULL,
  UNIQUE (series_ticker, event_ticker, rules_hash)
);

CREATE TABLE IF NOT EXISTS source_compatibility_rules (
  compatibility_key text PRIMARY KEY,
  series_rules_hash text NOT NULL,
  event_rules_hash text,
  weather_source text NOT NULL,
  report_type text NOT NULL,
  transformation_version text NOT NULL,
  status text NOT NULL CHECK (status IN ('unverified', 'proven', 'invalid')),
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  reviewed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS paper_book_checkpoints (
  id bigserial PRIMARY KEY,
  session_id text NOT NULL REFERENCES paper_sessions(id) ON DELETE CASCADE,
  connection_id uuid,
  market_ticker text NOT NULL,
  source_seq bigint NOT NULL,
  exchange_ts_ms bigint,
  received_epoch_ms bigint NOT NULL,
  captured_at timestamptz NOT NULL,
  reason text NOT NULL,
  yes_bids jsonb NOT NULL,
  no_bids jsonb NOT NULL,
  journal_message_id bigint REFERENCES market_data_journal(id),
  UNIQUE (session_id, connection_id, market_ticker, source_seq, reason)
);
CREATE INDEX IF NOT EXISTS paper_book_checkpoints_market_time_idx
  ON paper_book_checkpoints(market_ticker, received_epoch_ms);

ALTER TABLE paper_orders
  ADD COLUMN IF NOT EXISTS requested_qty_fp bigint,
  ADD COLUMN IF NOT EXISTS max_price_fp integer,
  ADD COLUMN IF NOT EXISTS filled_qty_fp bigint,
  ADD COLUMN IF NOT EXISTS gross_cost_micros bigint,
  ADD COLUMN IF NOT EXISTS fee_micros bigint,
  ADD COLUMN IF NOT EXISTS total_cost_micros bigint,
  ADD COLUMN IF NOT EXISTS state_known_through_epoch_ms bigint,
  ADD COLUMN IF NOT EXISTS execution_model_version text,
  ADD COLUMN IF NOT EXISTS fee_breakdown jsonb NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS paper_readiness_snapshots (
  captured_at timestamptz PRIMARY KEY,
  ready boolean NOT NULL,
  score integer NOT NULL,
  checks jsonb NOT NULL,
  blockers jsonb NOT NULL,
  model_version text NOT NULL
);
