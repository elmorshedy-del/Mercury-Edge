\set ON_ERROR_STOP on
BEGIN;

INSERT INTO paper_sessions(id,mode,model_version,status,config)
VALUES ('h4b-sql-test','replay','h4b-test','running','{}'::jsonb)
ON CONFLICT (id) DO NOTHING;

INSERT INTO raw_source_journal(
  id,capture_id,session_id,source,source_stream,station_code,
  received_at,received_epoch_ns,received_monotonic_ns,transport,
  content_type,raw_bytes,payload_sha256,metadata
) VALUES
  (919001,'raw:h4b-validation','h4b-sql-test','NWS','dsm','KNYC',
   '2026-08-22T05:30:00Z',1,1,'https_poll','application/json',decode('7b7d','hex'),repeat('a',64),'{}'::jsonb),
  (919002,'raw:h4b-settlement','h4b-sql-test','KALSHI','settlement','KNYC',
   '2026-08-22T12:00:00Z',2,2,'https_poll','application/json',decode('7b7d','hex'),repeat('b',64),'{}'::jsonb);

INSERT INTO validation_products(
  validation_id,session_id,source,source_product_id,station_code,climate_date,
  reported_max_f,max_observed_at,issued_at,mercury_received_at,raw_source_id,
  source_payload_sha256,lifecycle,authority,corrected,parser_version,
  validation_model_version,calendar_version,product_payload,product_sha256
) VALUES (
  'validation:h4b-v1','h4b-sql-test','NWS_DSM','dsm-product-1','KNYC','2026-08-21',
  77,'2026-08-21T19:25:00Z','2026-08-22T05:30:00Z','2026-08-22T05:30:01Z',919001,
  repeat('a',64),'completed_day_preliminary','corroboration_only',false,'dsm-v1',
  'validation-v1','lst-v1','{"v":1}'::jsonb,repeat('c',64)
);

INSERT INTO validation_products(
  validation_id,session_id,source,source_product_id,station_code,climate_date,
  reported_max_f,max_observed_at,issued_at,mercury_received_at,raw_source_id,
  source_payload_sha256,lifecycle,authority,corrected,revision_of,parser_version,
  validation_model_version,calendar_version,product_payload,product_sha256
) VALUES (
  'validation:h4b-v2','h4b-sql-test','NWS_DSM','dsm-product-2','KNYC','2026-08-21',
  78,'2026-08-21T19:25:00Z','2026-08-22T06:00:00Z','2026-08-22T06:00:01Z',919001,
  repeat('a',64),'completed_day_preliminary','corroboration_only',true,'validation:h4b-v1','dsm-v1',
  'validation-v1','lst-v1','{"v":2}'::jsonb,repeat('d',64)
);

DO $$
BEGIN
  IF (SELECT count(*) FROM validation_products WHERE session_id='h4b-sql-test') <> 2 THEN
    RAISE EXCEPTION 'validation revision coexistence regression';
  END IF;
END $$;

INSERT INTO authoritative_settlements(
  settlement_id,truth_id,session_id,event_ticker,station_code,climate_date,
  final_max_f,source,raw_source_id,rules_hash,rule_source_name,
  settlement_source_name,authority,observed_or_issued_at,truth_model_version,
  parser_version,settlement_payload,settlement_sha256
) VALUES (
  'settlement:h4b-v1','truth:h4b-v1','h4b-sql-test','KXHIGHNY-26AUG21','KNYC','2026-08-21',
  77,'KALSHI_EXCHANGE_RESULT',919002,repeat('e',64),'The Weather Company',
  'Kalshi exchange result','exchange_result','2026-08-22T12:00:00Z','truth-v1',
  'settlement-v1','{"truth":1}'::jsonb,repeat('f',64)
);

INSERT INTO settlement_audit_results(
  audit_id,session_id,settlement_id,severity,status,finding_code,station_code,
  climate_date,auditor_version,details,audit_payload,audit_sha256
) VALUES (
  'audit:h4b-v1','h4b-sql-test','settlement:h4b-v1','info','pass','H4B_FIXTURE_PASS','KNYC',
  '2026-08-21','settlement-auditor-v1','{}'::jsonb,'{"audit":1}'::jsonb,repeat('1',64)
);

DO $$
BEGIN
  BEGIN
    UPDATE validation_products SET reported_max_f=99 WHERE validation_id='validation:h4b-v1';
    RAISE EXCEPTION 'validation_products UPDATE unexpectedly succeeded';
  EXCEPTION WHEN SQLSTATE '55000' THEN NULL;
  END;

  BEGIN
    DELETE FROM validation_products WHERE validation_id='validation:h4b-v2';
    RAISE EXCEPTION 'validation_products DELETE unexpectedly succeeded';
  EXCEPTION WHEN SQLSTATE '55000' THEN NULL;
  END;

  BEGIN
    UPDATE authoritative_settlements SET final_max_f=99 WHERE settlement_id='settlement:h4b-v1';
    RAISE EXCEPTION 'authoritative_settlements UPDATE unexpectedly succeeded';
  EXCEPTION WHEN SQLSTATE '55000' THEN NULL;
  END;

  BEGIN
    DELETE FROM authoritative_settlements WHERE settlement_id='settlement:h4b-v1';
    RAISE EXCEPTION 'authoritative_settlements DELETE unexpectedly succeeded';
  EXCEPTION WHEN SQLSTATE '55000' THEN NULL;
  END;

  BEGIN
    UPDATE settlement_audit_results SET status='discrepancy' WHERE audit_id='audit:h4b-v1';
    RAISE EXCEPTION 'settlement_audit_results UPDATE unexpectedly succeeded';
  EXCEPTION WHEN SQLSTATE '55000' THEN NULL;
  END;

  BEGIN
    DELETE FROM settlement_audit_results WHERE audit_id='audit:h4b-v1';
    RAISE EXCEPTION 'settlement_audit_results DELETE unexpectedly succeeded';
  EXCEPTION WHEN SQLSTATE '55000' THEN NULL;
  END;
END $$;

ROLLBACK;
