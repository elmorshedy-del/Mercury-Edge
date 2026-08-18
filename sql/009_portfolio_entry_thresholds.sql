UPDATE paper_portfolio_mode_configs SET config = config || '{"max_no_price":0.90}'::jsonb WHERE mode_code='conservative';
UPDATE paper_portfolio_mode_configs SET config = config || '{"max_no_price":0.93}'::jsonb WHERE mode_code='balanced';
UPDATE paper_portfolio_mode_configs SET config = config || '{"max_no_price":0.95}'::jsonb WHERE mode_code='aggressive';
UPDATE paper_portfolio_mode_configs SET config = config || '{"max_no_price":0.97}'::jsonb WHERE mode_code='max_utilization';
UPDATE paper_portfolio_mode_configs SET config = config || '{"max_no_price":0.97}'::jsonb WHERE mode_code='depth_maximized';
UPDATE paper_portfolio_mode_configs SET config = config || '{"max_no_price":0.95}'::jsonb WHERE mode_code='best_edge_first';
