CREATE TABLE IF NOT EXISTS paper_sessions (
  id text PRIMARY KEY,
  mode text NOT NULL CHECK (mode IN ('paper_live', 'replay')),
  model_version text NOT NULL,
  started_at timestamptz NOT NULL DEFAULT now(),
  stopped_at timestamptz,
  status text NOT NULL DEFAULT 'running',
  config jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS market_data_journal (
  id bigserial PRIMARY KEY,
  session_id text NOT NULL REFERENCES paper_sessions(id) ON DELETE CASCADE,
  channel text NOT NULL,
  sid integer,
  seq bigint,
  market_ticker text,
  exchange_ts_ms bigint,
  received_at timestamptz NOT NULL,
  received_epoch_ms bigint NOT NULL,
  received_epoch_ns numeric(20,0) NOT NULL,
  received_monotonic_ns numeric(20,0) NOT NULL,
  payload jsonb NOT NULL,
  payload_sha256 text NOT NULL,
  UNIQUE(session_id, sid, seq, payload_sha256)
);
CREATE INDEX IF NOT EXISTS market_data_journal_market_time_idx
  ON market_data_journal(market_ticker, received_at, seq);

CREATE TABLE IF NOT EXISTS live_weather_journal (
  id bigserial PRIMARY KEY,
  session_id text NOT NULL REFERENCES paper_sessions(id) ON DELETE CASCADE,
  station_code text NOT NULL,
  source text NOT NULL,
  report_type text NOT NULL,
  observed_at timestamptz NOT NULL,
  source_received_at timestamptz,
  first_seen_at timestamptz NOT NULL,
  received_epoch_ms bigint NOT NULL,
  received_epoch_ns numeric(20,0) NOT NULL,
  received_monotonic_ns numeric(20,0) NOT NULL,
  temperature_f numeric(8,4),
  max_temperature_f numeric(8,4),
  raw_text text,
  raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  payload_sha256 text NOT NULL,
  compatibility_status text NOT NULL DEFAULT 'unverified'
    CHECK (compatibility_status IN ('unverified','proven','invalid')),
  compatibility_rule text,
  UNIQUE(session_id, station_code, source, report_type, observed_at, payload_sha256)
);
CREATE INDEX IF NOT EXISTS live_weather_journal_station_time_idx
  ON live_weather_journal(station_code, first_seen_at, observed_at);

CREATE TABLE IF NOT EXISTS paper_signals (
  id bigserial PRIMARY KEY,
  session_id text NOT NULL REFERENCES paper_sessions(id) ON DELETE CASCADE,
  station_code text NOT NULL,
  event_ticker text,
  strategy_code text NOT NULL,
  signal_class text NOT NULL,
  triggered_at timestamptz NOT NULL,
  trigger_epoch_ms bigint NOT NULL,
  trigger_epoch_ns numeric(20,0) NOT NULL,
  weather_event_id bigint REFERENCES live_weather_journal(id),
  state_hash text NOT NULL,
  evidence jsonb NOT NULL,
  auditor_status text NOT NULL CHECK (auditor_status IN ('approved','blocked','shadow_only')),
  blocked_reason text,
  UNIQUE(session_id, strategy_code, state_hash)
);

CREATE TABLE IF NOT EXISTS paper_orders (
  id bigserial PRIMARY KEY,
  session_id text NOT NULL REFERENCES paper_sessions(id) ON DELETE CASCADE,
  signal_id bigint NOT NULL REFERENCES paper_signals(id) ON DELETE CASCADE,
  latency_profile_ms integer NOT NULL,
  strategy_code text NOT NULL,
  market_ticker text NOT NULL,
  outcome_side text NOT NULL CHECK (outcome_side IN ('yes','no')),
  requested_qty numeric(14,4) NOT NULL,
  decision_at timestamptz NOT NULL,
  simulated_arrival_at timestamptz NOT NULL,
  book_seq bigint,
  status text NOT NULL CHECK (status IN ('filled','partial','unfilled','blocked')),
  avg_fill_price numeric(9,6),
  filled_qty numeric(14,4) NOT NULL DEFAULT 0,
  gross_cost numeric(14,6) NOT NULL DEFAULT 0,
  estimated_fee numeric(14,6) NOT NULL DEFAULT 0,
  worst_price numeric(9,6),
  book_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  audit jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS paper_orders_signal_idx ON paper_orders(signal_id, latency_profile_ms);

CREATE TABLE IF NOT EXISTS paper_fills (
  id bigserial PRIMARY KEY,
  order_id bigint NOT NULL REFERENCES paper_orders(id) ON DELETE CASCADE,
  price numeric(9,6) NOT NULL,
  qty numeric(14,4) NOT NULL,
  level_index integer NOT NULL,
  liquidity text NOT NULL DEFAULT 'taker',
  estimated_fee numeric(14,6) NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS audit_findings (
  id bigserial PRIMARY KEY,
  session_id text REFERENCES paper_sessions(id) ON DELETE CASCADE,
  severity text NOT NULL CHECK (severity IN ('info','warning','error','fatal')),
  component text NOT NULL,
  finding_code text NOT NULL,
  detected_at timestamptz NOT NULL DEFAULT now(),
  market_ticker text,
  station_code text,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  resolved_at timestamptz
);
CREATE INDEX IF NOT EXISTS audit_findings_open_idx
  ON audit_findings(severity, component, detected_at) WHERE resolved_at IS NULL;
