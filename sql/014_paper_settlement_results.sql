CREATE TABLE IF NOT EXISTS paper_market_results (
  market_ticker text PRIMARY KEY,
  event_ticker text,
  market_status text NOT NULL,
  result text CHECK (result IN ('yes','no','scalar') OR result IS NULL),
  is_provisional boolean,
  settlement_ts timestamptz,
  checked_at timestamptz NOT NULL DEFAULT now(),
  raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS paper_market_results_status_idx
  ON paper_market_results(market_status, checked_at DESC);

CREATE TABLE IF NOT EXISTS paper_shadow_trial_horizons (
  trial_id bigint NOT NULL REFERENCES paper_shadow_trials(id) ON DELETE CASCADE,
  horizon_ms bigint NOT NULL CHECK (horizon_ms >= 0),
  evaluated_at timestamptz NOT NULL DEFAULT now(),
  mark_epoch_ms bigint NOT NULL,
  exit_value numeric(14,6),
  exit_fee numeric(14,6),
  exit_pnl numeric(14,6),
  executable boolean NOT NULL,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (trial_id,horizon_ms)
);
CREATE INDEX IF NOT EXISTS paper_shadow_trial_horizons_eval_idx
  ON paper_shadow_trial_horizons(horizon_ms, executable, evaluated_at DESC);
