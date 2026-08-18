UPDATE paper_strategy_configs
SET paper_trade_enabled=true,
    shadow_enabled=true,
    config = config || CASE strategy_code
      WHEN 'DBN' THEN '{"engine_version":"dbn-v1","max_entry_price":0.97}'::jsonb
      WHEN 'DSN' THEN '{"engine_version":"dsn-v1","min_dead_markets":2,"allocation":"edge_weighted","max_entry_price":0.97}'::jsonb
      WHEN 'SBK' THEN '{"engine_version":"sbk-v1","min_net_edge":0.01,"require_mutually_exclusive":true,"equal_leg_quantity":true}'::jsonb
      WHEN 'HSR' THEN '{"engine_version":"hsr-v1","min_net_edge":0.01,"routes":["best_dbn","sbk"]}'::jsonb
      WHEN 'WTY' THEN '{"engine_version":"wty-v1","min_model_edge":0.03,"pre_signal_lookback_ms":5000,"max_yes_price":0.80,"model":"pre_signal_survivor_renormalization"}'::jsonb
      WHEN 'RMO' THEN '{"engine_version":"rmo-v1","stations":["KLAX"],"confirmation_delay_ms":1000,"min_momentum_cents":2,"max_yes_price":0.80,"coarse_unit":"whole_celsius"}'::jsonb
      WHEN 'PRV' THEN '{"engine_version":"prv-v1","stations":["KLAX"],"max_no_price":0.80,"coarse_unit":"whole_celsius","rule":"coarse_cross_not_precise_cross"}'::jsonb
      WHEN 'LVP' THEN '{"engine_version":"lvp-v1","stations":["KLAX"],"pair_window_minutes":180,"require_independent_rmo_and_prv":true}'::jsonb
      WHEN 'HMF' THEN '{"engine_version":"hmf-v1","stations":["KLAX"],"pair_window_minutes":240,"require_prior_rmo":true,"require_proven_hidden_max":true}'::jsonb
      ELSE '{}'::jsonb
    END,
    updated_at=now();

UPDATE paper_global_config
SET config = config || '{"all_strategy_paper_engines_enabled":true,"real_money_enabled":false}'::jsonb,
    updated_at=now()
WHERE id=1;
