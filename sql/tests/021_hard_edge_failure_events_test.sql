\set ON_ERROR_STOP on
BEGIN;

INSERT INTO paper_sessions(id,mode,model_version,status,config)
VALUES ('h4i-sql-test','replay','h4i-test','running','{}'::jsonb)
ON CONFLICT (id) DO NOTHING;

INSERT INTO hard_edge_failure_events(
  failure_id,session_id,stage,disposition_class,reason_code,station_code,
  climate_date,occurred_at,failure_model_version,details,failure_payload,failure_sha256
) VALUES
(
  'hard-edge-failure:h4i-integrity','h4i-sql-test','source_parse','integrity_failure',
  'ASOS_OFF_LATTICE_EVIDENCE','KNYC','2026-08-21','2026-08-21T18:00:00Z',
  'hard-edge-failure-ledger-v1','{"raw_group":"T0310"}'::jsonb,
  '{"event":"integrity"}'::jsonb,repeat('a',64)
),
(
  'hard-edge-failure:h4i-economic','h4i-sql-test','execution','economic_skip',
  'NO_POSITIVE_GUARANTEED_RETURN_AFTER_FEES','KNYC','2026-08-21','2026-08-21T18:00:01Z',
  'hard-edge-failure-ledger-v1','{}'::jsonb,
  '{"event":"economic"}'::jsonb,repeat('b',64)
);

DO $$
BEGIN
  IF (SELECT count(*) FROM hard_edge_failure_events WHERE session_id='h4i-sql-test') <> 2 THEN
    RAISE EXCEPTION 'failure ledger row count regression';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM hard_edge_failure_events
    WHERE session_id='h4i-sql-test'
      AND disposition_class='integrity_failure'
      AND reason_code='ASOS_OFF_LATTICE_EVIDENCE'
  ) THEN
    RAISE EXCEPTION 'integrity classification regression';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM hard_edge_failure_events
    WHERE session_id='h4i-sql-test'
      AND disposition_class='economic_skip'
      AND reason_code='NO_POSITIVE_GUARANTEED_RETURN_AFTER_FEES'
  ) THEN
    RAISE EXCEPTION 'economic-skip classification regression';
  END IF;

  BEGIN
    UPDATE hard_edge_failure_events
       SET reason_code='MUTATED'
     WHERE failure_id='hard-edge-failure:h4i-integrity';
    RAISE EXCEPTION 'hard_edge_failure_events UPDATE unexpectedly succeeded';
  EXCEPTION WHEN SQLSTATE '55000' THEN NULL;
  END;

  BEGIN
    DELETE FROM hard_edge_failure_events
     WHERE failure_id='hard-edge-failure:h4i-integrity';
    RAISE EXCEPTION 'hard_edge_failure_events DELETE unexpectedly succeeded';
  EXCEPTION WHEN SQLSTATE '55000' THEN NULL;
  END;
END $$;

ROLLBACK;
