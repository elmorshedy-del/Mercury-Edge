CREATE TABLE IF NOT EXISTS paper_strategy_configs (
  strategy_code text PRIMARY KEY,
  display_name text NOT NULL,
  family text NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  paper_trade_enabled boolean NOT NULL DEFAULT false,
  shadow_enabled boolean NOT NULL DEFAULT true,
  real_money_eligible boolean NOT NULL DEFAULT false,
  config jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS paper_global_config (
  id smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  config jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO paper_strategy_configs(strategy_code, display_name, family, enabled, paper_trade_enabled, shadow_enabled, real_money_eligible, config)
VALUES
  ('DBN','Dead-Bucket NO','hard_state',true,true,true,true,'{"confidence_gate":"approved_only","execution_overlay":"ITK"}'::jsonb),
  ('DSN','Dead-Set NO','hard_state',true,false,true,false,'{"confidence_gate":"shadow_only"}'::jsonb),
  ('SBK','Survivor Basket','hard_state',true,false,true,false,'{"confidence_gate":"shadow_only"}'::jsonb),
  ('HSR','Hard-State Router','hard_state',true,false,true,false,'{"confidence_gate":"shadow_only"}'::jsonb),
  ('WTY','Winner Transfer','probability_transfer',true,false,true,false,'{"confidence_gate":"shadow_only"}'::jsonb),
  ('RMO','Rounding Momentum','lax_precision',true,false,true,false,'{"confidence_gate":"shadow_only"}'::jsonb),
  ('PRV','Precision Reversal','lax_precision',true,false,true,false,'{"confidence_gate":"shadow_only"}'::jsonb),
  ('LVP','LAX V-Pair','sequential_lax',true,false,true,false,'{"confidence_gate":"shadow_only"}'::jsonb),
  ('HMF','Hidden-Max Flip','sequential_lax',true,false,true,false,'{"confidence_gate":"shadow_only"}'::jsonb)
ON CONFLICT (strategy_code) DO NOTHING;

INSERT INTO paper_global_config(id, config)
VALUES (1, '{"paper_enabled":true,"real_money_enabled":false,"starting_bankroll":1000,"all_modes_same_bankroll":true,"latency_ladder_ms":[0,10,25,50,100,250,500,1000,2000,5000],"default_execution_latency_ms":100,"max_signal_age_ms":5000,"require_proven_settlement_compatibility":true,"require_fresh_sequenced_l2":true,"require_verified_fee_model":true,"require_rule_snapshot":true}'::jsonb)
ON CONFLICT (id) DO NOTHING;
