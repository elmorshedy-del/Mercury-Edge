CREATE TABLE IF NOT EXISTS paper_portfolio_mode_configs (
  mode_code text PRIMARY KEY,
  display_name text NOT NULL,
  description text NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  starting_bankroll numeric(14,2) NOT NULL DEFAULT 1000.00 CHECK (starting_bankroll > 0),
  config jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS paper_portfolios (
  id bigserial PRIMARY KEY,
  session_id text NOT NULL REFERENCES paper_sessions(id) ON DELETE CASCADE,
  mode_code text NOT NULL REFERENCES paper_portfolio_mode_configs(mode_code),
  starting_bankroll numeric(14,2) NOT NULL,
  cash_balance numeric(14,6) NOT NULL,
  reserved_cash numeric(14,6) NOT NULL DEFAULT 0,
  realized_pnl numeric(14,6) NOT NULL DEFAULT 0,
  status text NOT NULL DEFAULT 'running' CHECK (status IN ('running','paused','stopped')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(session_id, mode_code)
);

ALTER TABLE paper_orders
  ADD COLUMN IF NOT EXISTS portfolio_id bigint REFERENCES paper_portfolios(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS paper_orders_portfolio_idx
  ON paper_orders(portfolio_id, decision_at DESC);

CREATE TABLE IF NOT EXISTS paper_positions (
  id bigserial PRIMARY KEY,
  portfolio_id bigint NOT NULL REFERENCES paper_portfolios(id) ON DELETE CASCADE,
  market_ticker text NOT NULL,
  outcome_side text NOT NULL CHECK (outcome_side IN ('yes','no')),
  qty numeric(14,4) NOT NULL DEFAULT 0,
  cost_basis numeric(14,6) NOT NULL DEFAULT 0,
  fees_paid numeric(14,6) NOT NULL DEFAULT 0,
  last_mark numeric(9,6),
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed','settled')),
  opened_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(portfolio_id, market_ticker, outcome_side)
);

CREATE TABLE IF NOT EXISTS paper_portfolio_decisions (
  id bigserial PRIMARY KEY,
  portfolio_id bigint NOT NULL REFERENCES paper_portfolios(id) ON DELETE CASCADE,
  signal_id bigint NOT NULL REFERENCES paper_signals(id) ON DELETE CASCADE,
  decided_at timestamptz NOT NULL DEFAULT now(),
  decision text NOT NULL CHECK (decision IN ('trade','skip','blocked')),
  requested_budget numeric(14,6) NOT NULL DEFAULT 0,
  score numeric(12,6),
  reason text,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE(portfolio_id, signal_id)
);

INSERT INTO paper_portfolio_mode_configs(mode_code, display_name, description, enabled, starting_bankroll, config)
VALUES
  ('conservative', 'Conservative', 'Low concentration, larger reserve, strict exposure caps.', true, 1000,
   '{"max_trade_pct":0.10,"max_event_pct":0.15,"max_region_pct":0.25,"max_daily_deployed_pct":0.40,"reserve_pct":0.40,"allocation":"equal_risk","execution_latency_ms":100,"tif":"fok"}'::jsonb),
  ('balanced', 'Balanced', 'Recommended baseline: meaningful utilization with moderate concentration controls.', true, 1000,
   '{"max_trade_pct":0.20,"max_event_pct":0.25,"max_region_pct":0.40,"max_daily_deployed_pct":0.70,"reserve_pct":0.20,"allocation":"edge_weighted","execution_latency_ms":100,"tif":"fok","recommended":true}'::jsonb),
  ('aggressive', 'Aggressive', 'Higher concentration and utilization while preserving event and regional caps.', true, 1000,
   '{"max_trade_pct":0.35,"max_event_pct":0.40,"max_region_pct":0.60,"max_daily_deployed_pct":1.00,"reserve_pct":0.05,"allocation":"edge_weighted","execution_latency_ms":100,"tif":"fok"}'::jsonb),
  ('max_utilization', 'Max Utilization', 'Deploy as much qualifying capital as possible without exceeding hard risk caps.', true, 1000,
   '{"max_trade_pct":0.50,"max_event_pct":0.60,"max_region_pct":0.80,"max_daily_deployed_pct":1.00,"reserve_pct":0.00,"allocation":"fill_available_capital","execution_latency_ms":100,"tif":"fok"}'::jsonb),
  ('depth_maximized', 'Depth Maximized', 'Size primarily to executable L2 capacity while respecting portfolio caps.', true, 1000,
   '{"max_trade_pct":0.50,"max_event_pct":0.60,"max_region_pct":0.80,"max_daily_deployed_pct":1.00,"reserve_pct":0.00,"allocation":"depth_first","execution_latency_ms":100,"tif":"fok"}'::jsonb),
  ('best_edge_first', 'Best Edge First', 'Concentrate capital in the highest ranked independent opportunities first.', true, 1000,
   '{"max_trade_pct":0.65,"max_event_pct":0.65,"max_region_pct":0.85,"max_daily_deployed_pct":1.00,"reserve_pct":0.00,"allocation":"best_edge_first","execution_latency_ms":100,"tif":"fok"}'::jsonb)
ON CONFLICT (mode_code) DO NOTHING;
