-- Original pre-day forecasts remain immutable: revised runs cannot erase observed errors.
CREATE TABLE IF NOT EXISTS weather_solar_forecasts (
  stid text NOT NULL,
  local_date date NOT NULL,
  model text NOT NULL,
  valid_at timestamptz NOT NULL,
  point jsonb NOT NULL,
  PRIMARY KEY(stid, local_date, model, valid_at)
);
