-- Step 4G-C2: immutable transport-continuity events.
--
-- Raw payloads themselves remain in raw_source_journal. This table records
-- transport facts that are not payloads (connect/reconnect/gap intervals,
-- sequence discontinuities, queue loss) so a replay can distinguish "no
-- weather happened" from "Mercury did not have continuous source coverage".

CREATE TABLE IF NOT EXISTS source_transport_events (
  event_id text PRIMARY KEY,
  session_id text NOT NULL REFERENCES paper_sessions(id) ON DELETE RESTRICT,
  source text NOT NULL,
  source_stream text NOT NULL,
  event_type text NOT NULL,
  connection_id text,
  detected_at timestamptz NOT NULL,
  detected_epoch_ns numeric(20,0) NOT NULL,
  detected_monotonic_ns numeric(20,0) NOT NULL,
  interval_start_at timestamptz,
  interval_end_at timestamptz,
  prior_sequence_key text,
  next_sequence_key text,
  model_version text NOT NULL,
  details jsonb NOT NULL DEFAULT '{}'::jsonb,
  event_sha256 text NOT NULL CHECK (event_sha256 ~ '^[0-9a-f]{64}$'),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS source_transport_events_stream_time_idx
  ON source_transport_events(session_id,source,source_stream,detected_at,event_id);
CREATE INDEX IF NOT EXISTS source_transport_events_connection_idx
  ON source_transport_events(connection_id,detected_at,event_id)
  WHERE connection_id IS NOT NULL;

-- The immutability function is created by migration 013. Transport-history
-- corrections are new rows; historical continuity facts are never rewritten.
DROP TRIGGER IF EXISTS source_transport_events_immutable ON source_transport_events;
CREATE TRIGGER source_transport_events_immutable
BEFORE UPDATE OR DELETE ON source_transport_events
FOR EACH ROW EXECUTE FUNCTION mercury_reject_immutable_mutation();
