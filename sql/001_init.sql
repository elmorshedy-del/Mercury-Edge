CREATE TABLE IF NOT EXISTS schema_migrations (
  version text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS stations (
  station_code text PRIMARY KEY,
  city text NOT NULL,
  timezone text NOT NULL,
  kalshi_series text NOT NULL UNIQUE,
  nws_location text,
  iem_network text,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS market_events (
  event_ticker text PRIMARY KEY,
  series_ticker text NOT NULL,
  station_code text REFERENCES stations(station_code),
  trade_date date NOT NULL,
  title text NOT NULL,
  settlement_source_name text,
  settlement_source_url text,
  rules_primary text,
  final_value_f numeric(6,2),
  settled_at timestamptz,
  raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  discovered_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS market_contracts (
  ticker text PRIMARY KEY,
  event_ticker text NOT NULL REFERENCES market_events(event_ticker) ON DELETE CASCADE,
  label text NOT NULL,
  lower_bound_f numeric(6,2),
  upper_bound_f numeric(6,2),
  result text CHECK (result IN ('yes', 'no', 'unknown')) DEFAULT 'unknown',
  raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS market_quotes (
  contract_ticker text NOT NULL REFERENCES market_contracts(ticker) ON DELETE CASCADE,
  captured_at timestamptz NOT NULL,
  yes_bid numeric(7,6),
  yes_ask numeric(7,6),
  last_price numeric(7,6),
  bid_size numeric(14,4),
  ask_size numeric(14,4),
  volume_delta numeric(14,4),
  open_interest numeric(14,4),
  source_precision text NOT NULL DEFAULT 'minute_candle',
  raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  ingested_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (contract_ticker, captured_at)
);

CREATE INDEX IF NOT EXISTS market_quotes_time_idx
  ON market_quotes(captured_at, contract_ticker);

CREATE TABLE IF NOT EXISTS weather_observations (
  id bigserial PRIMARY KEY,
  station_code text NOT NULL REFERENCES stations(station_code),
  source text NOT NULL,
  report_type text NOT NULL,
  observed_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL,
  receipt_quality text NOT NULL DEFAULT 'actual'
    CHECK (receipt_quality IN ('actual', 'bounded', 'discovery_only')),
  ingested_at timestamptz NOT NULL DEFAULT now(),
  temperature_f numeric(7,3),
  dewpoint_f numeric(7,3),
  wind_direction_deg integer,
  wind_speed_kt numeric(7,2),
  pressure_hpa numeric(8,2),
  sky_cover text,
  precipitation_in numeric(9,4),
  settlement_compatible boolean NOT NULL DEFAULT false,
  source_precision text,
  raw_text text,
  raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (station_code, source, report_type, observed_at, raw_text)
);

CREATE INDEX IF NOT EXISTS weather_observations_asof_idx
  ON weather_observations(station_code, received_at, observed_at);

CREATE TABLE IF NOT EXISTS product_releases (
  product_id text PRIMARY KEY,
  station_code text NOT NULL REFERENCES stations(station_code),
  product_type text NOT NULL,
  issued_at timestamptz NOT NULL,
  valid_through timestamptz,
  discovered_at timestamptz NOT NULL,
  reported_high_f numeric(6,2),
  high_observed_at timestamptz,
  source_url text NOT NULL,
  raw_text text NOT NULL,
  raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
  id bigserial PRIMARY KEY,
  started_at timestamptz NOT NULL,
  finished_at timestamptz,
  source text NOT NULL,
  station_code text,
  status text NOT NULL,
  records_written integer NOT NULL DEFAULT 0,
  error_message text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS backtest_runs (
  id bigserial PRIMARY KEY,
  name text NOT NULL,
  model_version text NOT NULL,
  started_at timestamptz NOT NULL,
  finished_at timestamptz,
  as_of_start timestamptz NOT NULL,
  as_of_end timestamptz NOT NULL,
  config jsonb NOT NULL,
  status text NOT NULL,
  summary jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS backtest_signals (
  id bigserial PRIMARY KEY,
  run_id bigint NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
  event_ticker text NOT NULL REFERENCES market_events(event_ticker),
  contract_ticker text NOT NULL REFERENCES market_contracts(ticker),
  station_code text NOT NULL REFERENCES stations(station_code),
  edge_class text NOT NULL,
  triggered_at timestamptz NOT NULL,
  trigger_observed_at timestamptz NOT NULL,
  running_high_f numeric(6,2) NOT NULL,
  official_floor_f numeric(6,2),
  action text NOT NULL,
  entry_price numeric(7,6),
  quote_captured_at timestamptz,
  reaction_at timestamptz,
  reaction_lag_seconds integer,
  outcome text,
  gross_profit_per_contract numeric(8,6),
  fee_per_contract numeric(8,6),
  net_profit_per_contract numeric(8,6),
  executable_proxy boolean NOT NULL DEFAULT false,
  limitation text,
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS backtest_signals_run_idx
  ON backtest_signals(run_id, edge_class, triggered_at);

CREATE TABLE IF NOT EXISTS source_health (
  source text NOT NULL,
  station_code text,
  last_success_at timestamptz,
  last_record_at timestamptz,
  last_error_at timestamptz,
  last_error text,
  consecutive_failures integer NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (source, station_code)
);
