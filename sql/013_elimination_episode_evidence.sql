ALTER TABLE weather_observations
  ADD COLUMN IF NOT EXISTS max_temperature_f numeric(7,3),
  ADD COLUMN IF NOT EXISTS max_temperature_kind text;

ALTER TABLE market_quotes
  ADD COLUMN IF NOT EXISTS yes_bid_open numeric(7,6),
  ADD COLUMN IF NOT EXISTS yes_bid_low numeric(7,6),
  ADD COLUMN IF NOT EXISTS yes_bid_high numeric(7,6),
  ADD COLUMN IF NOT EXISTS yes_ask_open numeric(7,6),
  ADD COLUMN IF NOT EXISTS yes_ask_low numeric(7,6),
  ADD COLUMN IF NOT EXISTS yes_ask_high numeric(7,6),
  ADD COLUMN IF NOT EXISTS last_price_open numeric(7,6),
  ADD COLUMN IF NOT EXISTS last_price_low numeric(7,6),
  ADD COLUMN IF NOT EXISTS last_price_high numeric(7,6);

CREATE TABLE IF NOT EXISTS market_trades (
  trade_id text PRIMARY KEY,
  contract_ticker text NOT NULL REFERENCES market_contracts(ticker) ON DELETE CASCADE,
  created_at timestamptz NOT NULL,
  yes_price numeric(7,6) NOT NULL,
  no_price numeric(7,6) NOT NULL,
  quantity numeric(14,4) NOT NULL,
  taker_outcome_side text CHECK (taker_outcome_side IN ('yes','no')),
  taker_book_side text CHECK (taker_book_side IN ('bid','ask')),
  is_block_trade boolean NOT NULL DEFAULT false,
  raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  ingested_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS market_trades_contract_time_idx
  ON market_trades(contract_ticker, created_at);
