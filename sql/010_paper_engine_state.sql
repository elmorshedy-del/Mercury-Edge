CREATE TABLE IF NOT EXISTS paper_engine_state (
  session_id text NOT NULL REFERENCES paper_sessions(id) ON DELETE CASCADE,
  component text NOT NULL,
  last_weather_id bigint NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(session_id, component)
);

UPDATE paper_global_config
SET config = config || '{"portfolio_day_timezone":"America/New_York","paper_engine_process_delay_ms":6000}'::jsonb,
    updated_at=now()
WHERE id=1;
