-- Every strategy has a real paper engine. These are paper-risk defaults only;
-- real-money execution remains hard-disabled and DBN remains the only Phase-1 candidate.
UPDATE paper_strategy_configs
SET paper_trade_enabled = true,
    shadow_enabled = true,
    config = config || CASE strategy_code
      WHEN 'DBN' THEN '{"max_portfolio_pct":1.00,"confidence_class":"hard_state","capital_priority":100}'::jsonb
      WHEN 'DSN' THEN '{"max_portfolio_pct":0.60,"confidence_class":"hard_state_multi_leg","capital_priority":90,"multi_leg_inter_order_ms":5}'::jsonb
      WHEN 'SBK' THEN '{"max_portfolio_pct":0.50,"confidence_class":"hard_state_multi_leg","capital_priority":85,"multi_leg_inter_order_ms":5,"min_net_edge":0.01}'::jsonb
      WHEN 'HSR' THEN '{"max_portfolio_pct":0.60,"confidence_class":"hard_state_router","capital_priority":95,"multi_leg_inter_order_ms":5}'::jsonb
      WHEN 'WTY' THEN '{"max_portfolio_pct":0.20,"confidence_class":"probabilistic","capital_priority":50,"min_model_edge":0.03}'::jsonb
      WHEN 'RMO' THEN '{"max_portfolio_pct":0.15,"confidence_class":"probabilistic_precision","capital_priority":40}'::jsonb
      WHEN 'PRV' THEN '{"max_portfolio_pct":0.15,"confidence_class":"precision_hypothesis","capital_priority":45}'::jsonb
      WHEN 'LVP' THEN '{"max_portfolio_pct":0.15,"confidence_class":"sequential_hypothesis","capital_priority":35,"multi_leg_inter_order_ms":5}'::jsonb
      WHEN 'HMF' THEN '{"max_portfolio_pct":0.20,"confidence_class":"hard_state_sequence","capital_priority":55,"multi_leg_inter_order_ms":5}'::jsonb
      ELSE '{}'::jsonb
    END,
    updated_at = now();

UPDATE paper_global_config
SET config = config || '{"all_strategy_paper_engines_enabled":true,"real_money_enabled":false,"strategy_capital_priority":"hard_state_first"}'::jsonb,
    updated_at = now()
WHERE id=1;
