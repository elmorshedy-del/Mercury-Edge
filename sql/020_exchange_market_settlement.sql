-- Step 4H-D prerequisite: exchange settlement can authoritatively resolve a
-- contract without Mercury proving that an API field is the physical final
-- temperature. Keep numeric final max optional and preserve exact market
-- results separately.

ALTER TABLE authoritative_settlements
  ALTER COLUMN final_max_f DROP NOT NULL;

ALTER TABLE authoritative_settlements
  ADD COLUMN IF NOT EXISTS settlement_kind text NOT NULL DEFAULT 'numeric_final'
    CHECK (settlement_kind IN ('numeric_final','exchange_market_results','combined')),
  ADD COLUMN IF NOT EXISTS market_results jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE authoritative_settlements
  DROP CONSTRAINT IF EXISTS authoritative_settlements_truth_shape;
ALTER TABLE authoritative_settlements
  ADD CONSTRAINT authoritative_settlements_truth_shape CHECK (
    final_max_f IS NOT NULL
    OR jsonb_array_length(market_results) > 0
  );
