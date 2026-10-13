-- Preserve point extractions so replay does not repeatedly download large NOAA grids.
CREATE TABLE IF NOT EXISTS weather_solar_samples (
  stid text NOT NULL,
  sample_at timestamptz NOT NULL,
  sample jsonb NOT NULL,
  PRIMARY KEY (stid, sample_at)
);
