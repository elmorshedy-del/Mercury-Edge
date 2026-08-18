-- Paper-rigour v2: keep benchmark bankrolls isolated while allowing a much
-- broader counterfactual experiment matrix that never spends benchmark cash.

ALTER TABLE paper_positions
  ADD COLUMN IF NOT EXISTS settlement_result text CHECK (settlement_result IN ('yes','no')),
  ADD COLUMN IF NOT EXISTS settled_payout numeric(14,6),
  ADD COLUMN IF NOT EXISTS realized_pnl numeric(14,6),
  ADD COLUMN IF NOT EXISTS settled_at timestamptz;

CREATE TABLE IF NOT EXISTS paper_shadow_trials (
  id bigserial PRIMARY KEY,
  session_id text NOT NULL REFERENCES paper_sessions(id) ON DELETE CASCADE,
  signal_id bigint NOT NULL REFERENCES paper_signals(id) ON DELETE CASCADE,
  strategy_code text NOT NULL,
  variant text NOT NULL,
  latency_ms integer NOT NULL CHECK (latency_ms >= 0),
  budget numeric(14,6) NOT NULL CHECK (budget > 0),
  status text NOT NULL CHECK (status IN ('filled','blocked','unfilled','settled')),
  gross_cost numeric(14,6) NOT NULL DEFAULT 0,
  estimated_fee numeric(14,6) NOT NULL DEFAULT 0,
  expected_payout numeric(14,6),
  expected_edge numeric(14,6),
  settled_payout numeric(14,6),
  realized_pnl numeric(14,6),
  settled_at timestamptz,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(session_id, signal_id, variant, latency_ms, budget)
);
CREATE INDEX IF NOT EXISTS paper_shadow_trials_strategy_idx
  ON paper_shadow_trials(strategy_code, status, created_at DESC);

CREATE TABLE IF NOT EXISTS paper_risk_state (
  portfolio_id bigint PRIMARY KEY REFERENCES paper_portfolios(id) ON DELETE CASCADE,
  high_watermark numeric(14,6) NOT NULL,
  equity numeric(14,6) NOT NULL,
  drawdown_pct numeric(12,8) NOT NULL DEFAULT 0,
  risk_multiplier numeric(8,6) NOT NULL DEFAULT 1,
  state text NOT NULL DEFAULT 'normal' CHECK (state IN ('normal','caution','reduced','paused','mark_incomplete')),
  mark_complete boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS paper_equity_snapshots (
  portfolio_id bigint NOT NULL REFERENCES paper_portfolios(id) ON DELETE CASCADE,
  captured_at timestamptz NOT NULL DEFAULT now(),
  cash_balance numeric(14,6) NOT NULL,
  open_mark_value numeric(14,6) NOT NULL,
  equity numeric(14,6) NOT NULL,
  high_watermark numeric(14,6) NOT NULL,
  drawdown_pct numeric(12,8) NOT NULL,
  risk_multiplier numeric(8,6) NOT NULL,
  risk_state text NOT NULL,
  mark_complete boolean NOT NULL,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (portfolio_id, captured_at)
);
CREATE INDEX IF NOT EXISTS paper_equity_snapshots_time_idx
  ON paper_equity_snapshots(captured_at DESC, portfolio_id);

-- Every aggressiveness mode is an independent alternative universe.  They all
-- begin at exactly $1,000; no cash or exposure is shared between portfolio IDs.
UPDATE paper_portfolio_mode_configs
SET starting_bankroll=1000.00,
    updated_at=now()
WHERE enabled;

-- Drawdown thresholds differ deliberately by sleeve.  These are benchmark risk
-- policies, not claims of optimality; the shadow lab lets us compare alternatives.
UPDATE paper_portfolio_mode_configs
SET config = config || CASE mode_code
  WHEN 'conservative' THEN '{"drawdown_policy":{"caution":0.04,"reduced":0.08,"pause":0.12,"caution_multiplier":0.65,"reduced_multiplier":0.35}}'::jsonb
  WHEN 'balanced' THEN '{"drawdown_policy":{"caution":0.05,"reduced":0.10,"pause":0.15,"caution_multiplier":0.75,"reduced_multiplier":0.50}}'::jsonb
  WHEN 'aggressive' THEN '{"drawdown_policy":{"caution":0.075,"reduced":0.15,"pause":0.25,"caution_multiplier":0.80,"reduced_multiplier":0.60}}'::jsonb
  WHEN 'max_utilization' THEN '{"drawdown_policy":{"caution":0.10,"reduced":0.20,"pause":0.30,"caution_multiplier":0.85,"reduced_multiplier":0.65}}'::jsonb
  WHEN 'depth_maximized' THEN '{"drawdown_policy":{"caution":0.10,"reduced":0.20,"pause":0.30,"caution_multiplier":0.85,"reduced_multiplier":0.65}}'::jsonb
  WHEN 'best_edge_first' THEN '{"drawdown_policy":{"caution":0.08,"reduced":0.16,"pause":0.25,"caution_multiplier":0.80,"reduced_multiplier":0.55}}'::jsonb
  ELSE '{}'::jsonb
END,
updated_at=now();

UPDATE paper_strategy_configs
SET config = config || '{"prior_max_age_seconds":180}'::jsonb,
    updated_at=now()
WHERE strategy_code='RMO';

UPDATE paper_global_config
SET config = config || '{
  "strict_shadow_isolation":true,
  "paper_experiment_lab_enabled":true,
  "experiment_latency_ladder_ms":[0,10,25,50,100,250,500,1000,2000,5000],
  "experiment_budget_ladder":[10,25,50,100,200],
  "drawdown_risk_enabled":true,
  "risk_state_max_age_seconds":30,
  "mark_incomplete_blocks_new_entries":true,
  "settlement_cash_recycling":true
}'::jsonb,
updated_at=now()
WHERE id=1;
