\set ON_ERROR_STOP on

BEGIN;

INSERT INTO paper_sessions(id,mode,model_version,status)
VALUES ('ci-transport-events','replay','ci','running')
ON CONFLICT (id) DO NOTHING;

INSERT INTO source_transport_events(
  event_id,session_id,source,source_stream,event_type,connection_id,
  detected_at,detected_epoch_ns,detected_monotonic_ns,
  interval_start_at,interval_end_at,prior_sequence_key,next_sequence_key,
  model_version,details,event_sha256
) VALUES (
  'transport:test-gap','ci-transport-events','MADIS_OMO','madis_omo_ldm',
  'sequence_gap','ldm-1','2026-08-20T17:00:05Z',1755709205000000000,
  123456789,'2026-08-20T17:00:01Z','2026-08-20T17:00:05Z',
  'seq:100','seq:105','source-transport-events-v1',
  '{"reason":"fixture"}'::jsonb,
  '1a509322811d739c8ede19ff4094e52ff514c64980c5da8706630bbe0fa35da9'
);

DO $$
BEGIN
  BEGIN
    UPDATE source_transport_events
       SET details='{"mutated":true}'::jsonb
     WHERE event_id='transport:test-gap';
    RAISE EXCEPTION 'UPDATE unexpectedly succeeded';
  EXCEPTION WHEN SQLSTATE '55000' THEN
    NULL;
  END;

  BEGIN
    DELETE FROM source_transport_events
     WHERE event_id='transport:test-gap';
    RAISE EXCEPTION 'DELETE unexpectedly succeeded';
  EXCEPTION WHEN SQLSTATE '55000' THEN
    NULL;
  END;
END $$;

DO $$
DECLARE
  n integer;
BEGIN
  SELECT count(*) INTO n
    FROM source_transport_events
   WHERE event_id='transport:test-gap';
  IF n <> 1 THEN
    RAISE EXCEPTION 'immutable transport event row missing after mutation tests';
  END IF;
END $$;

ROLLBACK;
